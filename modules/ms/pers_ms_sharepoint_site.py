"""
This module contains the persistence layer for the BuildOne vendor.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
import pyodbc

import persistence.pers_database as pers_database
from persistence.pers_response import DatabaseError, SuccessResponse, PersistenceResponse


@dataclass
class SharePointSite:
    """Represents a SharePoint site in the system."""
    site_id: Optional[int] = None
    site_guid: Optional[str] = None
    site_created_datetime: Optional[datetime] = None
    site_modified_datetime: Optional[datetime] = None
    site_o_data_context: Optional[str] = None
    site_description: Optional[str] = None
    site_display_name: Optional[str] = None
    site_sharepoint_id: Optional[str] = None
    site_last_modified_datetime: Optional[datetime] = None
    site_name: Optional[str] = None
    site_root: Optional[str] = None
    site_collection_host_name: Optional[str] = None
    site_web_url: Optional[str] = None

    @classmethod
    def from_db_row(cls, row) -> 'SharePointSite':
        """Creates a SharePointSite object from a database row."""
        if not row:
            return None

        return cls(
            site_id=getattr(row, 'Id'),
            site_guid=getattr(row, 'GUID'),
            site_created_datetime=getattr(row, 'CreatedDatetime'),
            site_modified_datetime=getattr(row, 'ModifiedDatetime'),
            site_o_data_context=getattr(row, 'ODataContext'),
            site_description=getattr(row, 'Description'),
            site_display_name=getattr(row, 'DisplayName'),
            site_sharepoint_id=getattr(row, 'SiteId'),
            site_last_modified_datetime=getattr(row, 'LastModifiedDatetime'),
            site_name=getattr(row, 'Name'),
            site_root=getattr(row, 'Root'),
            site_collection_host_name=getattr(row, 'SiteCollectionHostName'),
            site_web_url=getattr(row, 'WebUrl')
        )


def create_sharepoint_site(sharepoint_site: SharePointSite):
    """
    Creates a SharePoint site in the database.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL CreateMsSharePointSite (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)}"
                rowcount = cursor.execute(
                    sql,
                    sharepoint_site.site_created_datetime,
                    sharepoint_site.site_modified_datetime,
                    sharepoint_site.site_o_data_context,
                    sharepoint_site.site_description,
                    sharepoint_site.site_display_name,
                    sharepoint_site.site_sharepoint_id,
                    sharepoint_site.site_last_modified_datetime,
                    sharepoint_site.site_name,
                    sharepoint_site.site_root,
                    sharepoint_site.site_collection_host_name,
                    sharepoint_site.site_web_url
                ).rowcount

                if rowcount > 0:
                    return SuccessResponse(message="SharePoint site created", status_code=200)

                return PersistenceResponse(message="SharePoint site not created", status_code=400)

        except pyodbc.Error as e:
            raise DatabaseError(f"Failed to create SharePoint site: {str(e)}") from e


def read_sharepoint_sites():
    """
    Retrieves all SharePoint sites from the database.

    Returns:
        List[SharePointSite]: A list of SharePointSite objects
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadMsSharePointSites}"
                rows = cursor.execute(sql).fetchall()

                if rows:
                    return SuccessResponse(
                        message="SharePoint sites found",
                        data=[SharePointSite.from_db_row(row) for row in rows],
                        status_code=200
                    )

                return PersistenceResponse(message="No SharePoint sites found", status_code=404)


        except pyodbc.Error as e:
            raise DatabaseError(f"Failed to read SharePoint sites: {str(e)}") from e


def read_sharepoint_site_by_site_id(site_id: str):
    """
    Retrieves a SharePoint site by site id from the database.

    Returns:
        SharePointSite: A SharePointSite object
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadMsSharePointSiteBySiteId(?)}"
                row = cursor.execute(sql, site_id).fetchone()

                if row:
                    return SuccessResponse(
                        message="SharePoint site found",
                        data=SharePointSite.from_db_row(row),
                        status_code=200
                    )

                return PersistenceResponse(message="SharePoint site not found", status_code=404)


        except pyodbc.Error as e:
            raise DatabaseError(f"Failed to read SharePoint site: {str(e)}") from e


def update_sharepoint_site_by_site_id(sharepoint_site: SharePointSite):
    """
    Updates a SharePoint site by site id in the database.
    """
    """
    Creates a SharePoint site in the database.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL UpdateMsSharePointSiteBySiteId (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)}"
                rowcount = cursor.execute(
                    sql,
                    sharepoint_site.site_modified_datetime,
                    sharepoint_site.site_o_data_context,
                    sharepoint_site.site_description,
                    sharepoint_site.site_display_name,
                    sharepoint_site.site_sharepoint_id,
                    sharepoint_site.site_last_modified_datetime,
                    sharepoint_site.site_name,
                    sharepoint_site.site_root,
                    sharepoint_site.site_collection_host_name,
                    sharepoint_site.site_web_url
                ).rowcount

                if rowcount > 0:
                    return SuccessResponse(message="SharePoint site updated", status_code=200)

                return PersistenceResponse(message="SharePoint site not updated", status_code=400)


        except pyodbc.Error as e:
            raise DatabaseError(f"Failed to update SharePoint site: {str(e)}") from e
