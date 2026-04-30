"""
Active-Company resolution for the auth flow.

At login / refresh / switch-company time we need to figure out which
Company the user's JWT should carry as `cid`. The rules are:

- System admins (User.IsSystemAdmin = 1) can pick ANY Company.
- Regular users can only pick a Company they have a UserCompany row for.
- If the user has User.LastCompanyId set AND it's still accessible,
  prefer that (so re-login lands them in the same place they left).
- Otherwise pick the lowest-id accessible Company.
- Regular users with zero accessible Companies → return None (login
  caller rejects per Q7).

Reads bypass the entity stored procedures because Phase 0 hasn't yet
extended UserCompany / Company sprocs with the joined fields the auth
flow needs (Company name + parent Organization). Direct SELECTs are
used to keep the Phase 0 surface small. Phase 1+ will move this onto
proper sprocs as part of the per-Company sproc rewrites.
"""
from __future__ import annotations

import logging
from typing import Optional, NamedTuple

from shared.database import get_connection

logger = logging.getLogger(__name__)


class ActiveCompany(NamedTuple):
    id: int
    public_id: str
    name: str
    organization_id: Optional[int]
    organization_public_id: Optional[str]
    organization_name: Optional[str]


class CompanyChoice(NamedTuple):
    public_id: str
    name: str
    organization_public_id: Optional[str]
    organization_name: Optional[str]


def resolve_active_company_for_user(user_id: int) -> Optional[ActiveCompany]:
    """
    Pick the user's active Company at login / refresh time.

    Returns None for regular users with zero UserCompany rows. System
    admins always resolve to a Company (lowest-id Company in the system
    if no LastCompanyId).
    """
    try:
        with get_connection() as conn:
            cursor = conn.cursor()

            # Fetch the user's discriminator state.
            cursor.execute(
                "SELECT [LastCompanyId], [IsSystemAdmin] FROM dbo.[User] WHERE [Id] = ?",
                (user_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            last_company_id = row[0]
            is_system_admin = bool(row[1])

            company = None

            # Try LastCompanyId first.
            if last_company_id is not None:
                if is_system_admin:
                    company = _read_company_with_org(cursor, last_company_id)
                else:
                    company = _read_user_company(
                        cursor,
                        user_id=user_id,
                        company_id=last_company_id,
                    )

            if company is not None:
                return company

            # Fall back to first accessible.
            if is_system_admin:
                cursor.execute(
                    "SELECT TOP 1 [Id] FROM dbo.[Company] ORDER BY [Id] ASC"
                )
                r = cursor.fetchone()
                if r:
                    company = _read_company_with_org(cursor, r[0])
            else:
                cursor.execute(
                    """SELECT TOP 1 c.[Id]
                         FROM dbo.[Company] c
                         JOIN dbo.[UserCompany] uc ON uc.[CompanyId] = c.[Id]
                        WHERE uc.[UserId] = ?
                        ORDER BY uc.[Id] ASC""",
                    (user_id,),
                )
                r = cursor.fetchone()
                if r:
                    company = _read_company_with_org(cursor, r[0])

            return company
    except Exception as error:
        logger.error(f"Error resolving active company for user {user_id}: {error}")
        return None


def list_accessible_companies(user_id: int) -> list[CompanyChoice]:
    """
    Companies the user can see in the picker. System admins see every
    Company in the system; regular users see only their UserCompany rows.
    """
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT [IsSystemAdmin] FROM dbo.[User] WHERE [Id] = ?",
                (user_id,),
            )
            row = cursor.fetchone()
            if not row:
                return []
            is_system_admin = bool(row[0])

            if is_system_admin:
                cursor.execute(
                    """SELECT c.[PublicId], c.[Name], o.[PublicId], o.[Name]
                         FROM dbo.[Company] c
                         LEFT JOIN dbo.[Organization] o ON o.[Id] = c.[OrganizationId]
                        ORDER BY c.[Name] ASC"""
                )
            else:
                cursor.execute(
                    """SELECT c.[PublicId], c.[Name], o.[PublicId], o.[Name]
                         FROM dbo.[Company] c
                         JOIN dbo.[UserCompany] uc ON uc.[CompanyId] = c.[Id]
                         LEFT JOIN dbo.[Organization] o ON o.[Id] = c.[OrganizationId]
                        WHERE uc.[UserId] = ?
                        ORDER BY c.[Name] ASC""",
                    (user_id,),
                )

            return [
                CompanyChoice(
                    public_id=str(r[0]),
                    name=r[1],
                    organization_public_id=str(r[2]) if r[2] is not None else None,
                    organization_name=r[3],
                )
                for r in cursor.fetchall()
            ]
    except Exception as error:
        logger.error(f"Error listing accessible companies for user {user_id}: {error}")
        return []


def resolve_company_by_public_id_for_user(
    *,
    user_id: int,
    company_public_id: str,
) -> Optional[ActiveCompany]:
    """
    Validate that the user is allowed to switch to the requested
    Company, and return its ActiveCompany payload. System admins can
    switch to any Company; regular users must have a UserCompany row.
    Returns None on validation failure.
    """
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT [IsSystemAdmin] FROM dbo.[User] WHERE [Id] = ?",
                (user_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            is_system_admin = bool(row[0])

            if is_system_admin:
                cursor.execute(
                    "SELECT [Id] FROM dbo.[Company] WHERE [PublicId] = ?",
                    (company_public_id,),
                )
            else:
                cursor.execute(
                    """SELECT c.[Id]
                         FROM dbo.[Company] c
                         JOIN dbo.[UserCompany] uc ON uc.[CompanyId] = c.[Id]
                        WHERE c.[PublicId] = ? AND uc.[UserId] = ?""",
                    (company_public_id, user_id),
                )
            r = cursor.fetchone()
            if not r:
                return None
            return _read_company_with_org(cursor, r[0])
    except Exception as error:
        logger.error(
            f"Error resolving company {company_public_id} for user {user_id}: {error}"
        )
        return None


def _read_user_company(cursor, *, user_id: int, company_id: int) -> Optional[ActiveCompany]:
    cursor.execute(
        """SELECT c.[Id]
             FROM dbo.[Company] c
             JOIN dbo.[UserCompany] uc ON uc.[CompanyId] = c.[Id]
            WHERE c.[Id] = ? AND uc.[UserId] = ?""",
        (company_id, user_id),
    )
    r = cursor.fetchone()
    if not r:
        return None
    return _read_company_with_org(cursor, r[0])


def _read_company_with_org(cursor, company_id: int) -> Optional[ActiveCompany]:
    cursor.execute(
        """SELECT c.[Id], c.[PublicId], c.[Name], c.[OrganizationId],
                  o.[PublicId], o.[Name]
             FROM dbo.[Company] c
             LEFT JOIN dbo.[Organization] o ON o.[Id] = c.[OrganizationId]
            WHERE c.[Id] = ?""",
        (company_id,),
    )
    r = cursor.fetchone()
    if not r:
        return None
    return ActiveCompany(
        id=r[0],
        public_id=str(r[1]),
        name=r[2],
        organization_id=r[3],
        organization_public_id=str(r[4]) if r[4] is not None else None,
        organization_name=r[5],
    )
