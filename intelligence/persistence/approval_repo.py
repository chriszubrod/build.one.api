"""Persistence for agent approval requests.

One row per requires_approval tool invocation that paused for user
decision. Written Status='pending' when the loop pauses; updated to
approved / rejected / timed_out when the decision arrives.
"""
import base64
import logging
from typing import Optional

import pyodbc
from pydantic import BaseModel

from shared.database import call_procedure, get_connection, map_database_error


logger = logging.getLogger(__name__)


class AgentApprovalRequest(BaseModel):
    id: Optional[int] = None
    public_id: Optional[str] = None
    row_version: Optional[str] = None
    created_datetime: Optional[str] = None
    modified_datetime: Optional[str] = None
    session_id: Optional[int] = None
    turn_id: Optional[int] = None
    request_id: Optional[str] = None
    tool_name: Optional[str] = None
    summary: Optional[str] = None
    proposed_input: Optional[str] = None
    status: Optional[str] = None
    final_input: Optional[str] = None
    decided_by_user_id: Optional[int] = None
    decided_at: Optional[str] = None


def _encode_row_version(rv) -> Optional[str]:
    if rv is None:
        return None
    return base64.b64encode(rv).decode("ascii")


def _public_id_str(pid) -> Optional[str]:
    return str(pid) if pid is not None else None


class AgentApprovalRequestRepo:
    def _from_db(self, row: pyodbc.Row) -> Optional[AgentApprovalRequest]:
        if not row:
            return None
        try:
            return AgentApprovalRequest(
                id=row.Id,
                public_id=_public_id_str(row.PublicId),
                row_version=_encode_row_version(row.RowVersion),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                session_id=row.SessionId,
                turn_id=row.TurnId,
                request_id=row.RequestId,
                tool_name=row.ToolName,
                summary=row.Summary,
                proposed_input=row.ProposedInput,
                status=row.Status,
                final_input=row.FinalInput,
                decided_by_user_id=row.DecidedByUserId,
                decided_at=row.DecidedAt,
            )
        except Exception as error:
            logger.error(f"AgentApprovalRequest._from_db failed: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        session_id: int,
        request_id: str,
        tool_name: str,
        proposed_input: str,
        turn_id: Optional[int] = None,
        summary: Optional[str] = None,
    ) -> AgentApprovalRequest:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateAgentApprovalRequest",
                    params={
                        "SessionId": session_id,
                        "TurnId": turn_id,
                        "RequestId": request_id,
                        "ToolName": tool_name,
                        "Summary": summary,
                        "ProposedInput": proposed_input,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    raise map_database_error(
                        Exception("CreateAgentApprovalRequest returned no row")
                    )
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error creating approval request: {error}")
            raise map_database_error(error)

    def set_decision(
        self,
        *,
        id: int,
        status: str,
        final_input: Optional[str] = None,
        decided_by_user_id: Optional[int] = None,
    ) -> Optional[AgentApprovalRequest]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="SetAgentApprovalRequestDecision",
                    params={
                        "Id": id,
                        "Status": status,
                        "FinalInput": final_input,
                        "DecidedByUserId": decided_by_user_id,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error setting approval decision: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[AgentApprovalRequest]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadAgentApprovalRequestById",
                    params={"Id": id},
                )
                return self._from_db(cursor.fetchone())
        except Exception as error:
            logger.error(f"Error reading approval by id: {error}")
            raise map_database_error(error)

    def read_by_public_id(
        self, public_id: str
    ) -> Optional[AgentApprovalRequest]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadAgentApprovalRequestByPublicId",
                    params={"PublicId": public_id},
                )
                return self._from_db(cursor.fetchone())
        except Exception as error:
            logger.error(f"Error reading approval by public_id: {error}")
            raise map_database_error(error)

    def read_by_session_request(
        self, session_id: int, request_id: str
    ) -> Optional[AgentApprovalRequest]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadAgentApprovalRequestBySessionRequest",
                    params={
                        "SessionId": session_id,
                        "RequestId": request_id,
                    },
                )
                return self._from_db(cursor.fetchone())
        except Exception as error:
            logger.error(
                f"Error reading approval by session/request: {error}"
            )
            raise map_database_error(error)

    def read_by_session_id(
        self, session_id: int
    ) -> list[AgentApprovalRequest]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadAgentApprovalRequestsBySessionId",
                    params={"SessionId": session_id},
                )
                return [self._from_db(r) for r in cursor.fetchall() if r]
        except Exception as error:
            logger.error(f"Error reading approvals by session id: {error}")
            raise map_database_error(error)
