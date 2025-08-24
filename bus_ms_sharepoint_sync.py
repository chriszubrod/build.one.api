"""
Service module for syncing with Microsoft SharePoint.
"""

# python standard library imports
import asyncio
import base64
import hashlib
import re
import requests
from datetime import datetime
from dateutil import tz
from difflib import SequenceMatcher
from typing import List, Dict, Any
from flask import session


# third party imports
import jwt
import time

# local imports
from business.bus_response import BusinessResponse
from integrations.map import (
    pers_map_attachment_sharepoint_file,
    pers_map_project_sharepoint_folder
)
from integrations.ms import (
    ms_drive_int,
    ms_upload_new_file,
    pers_ms_sharepoint_file,
    pers_ms_sharepoint_folder,
    pers_ms_sharepoint_site
)
from integrations.ms.auth import bus_ms_auth, api_ms_auth
from modules.bill import pers_bill_line_item, pers_bill_line_item_attachment
from modules.project import pers_project


def _get_bill_line_item_attachments():
    bill_line_item_attachments = pers_bill_line_item_attachment.\
        get_bill_line_item_attachments()
    if bill_line_item_attachments.success:
        return bill_line_item_attachments.data
    else:
        return None


def _get_bill_line_item_attachments_mapping():
    bill_line_item_attachments_mapping = pers_map_attachment_sharepoint_file.\
        read_map_attachment_sharepoint_files()
    if bill_line_item_attachments_mapping.success:
        return bill_line_item_attachments_mapping.data
    else:
        return None


def _get_bill_line_item_attachments_not_mapped(attachments, mapping):
    print(f'\nAttachments Type: {type(attachments)}')
    print(f'\nMapping Type: {type(mapping)}')
    return [attachment for attachment in attachments if attachment.id not in [mapping.bill_line_item_attachment_id for mapping in mapping]]


def _get_bill_line_item_by_bill_line_item_id(bill_line_item_id):
    bill_line_item = pers_bill_line_item.\
        read_bill_line_item_by_id(bill_line_item_id)
    if bill_line_item.success:
        return bill_line_item.data
    else:
        return None


def _get_project_integration_sharepoint_folder_mapping(project_id, module_id=4):
    project_sharepoint_folder_mapping = pers_map_project_sharepoint_folder.\
        read_map_project_sharepoint_folders_by_project_by_module(
            project_id=project_id,
            module_id=module_id
        )
    if project_sharepoint_folder_mapping.success:
        return project_sharepoint_folder_mapping.data
    else:
        return None


def _get_sharepoint_site():
    sharepoint_site = pers_ms_sharepoint_site.\
        read_sharepoint_sites()
    if sharepoint_site.success:
        return sharepoint_site.data[0]
    else:
        return None


def _get_sharepoint_access_token():
    ms_auth = bus_ms_auth.get_ms_auth_by_user_id(
        user_id=2
    )
    if ms_auth.success:
        return ms_auth.data.access_token
    else:
        return None


def _get_sharepoint_folder_by_folder_id(folder_id):
    sharepoint_folder = pers_ms_sharepoint_folder.\
        read_sharepoint_folder_by_folder_id(folder_id)
    if sharepoint_folder.success:
        return sharepoint_folder.data
    else:
        return None


def _get_sharepoint_files(site_id, folder_id, access_token):
    files = ms_drive_int.get_items(
        site_id=site_id,
        item_id=folder_id,
        access_token=access_token
    )
    print(f'\nFiles: {files[0]}')
    return files


def _match_sharepoint_file_to_bill_line_item_attachment(sharepoint_files, bill_line_item_attachment):
    # TODO: Use SequenceMatcher to match the bill line item attachment to the sharepoint files
    bill_line_item_attachment_name = bill_line_item_attachment.name.lower()

    sharepoint_files_dict = {}
    for i, file in enumerate(sharepoint_files, 1):
        file_name = file.get('name', '').lower()
        sharepoint_files_dict[file_name] = {
            'index': i,
            'file': file,
            'file_name': file_name
        }

    best_match = None
    best_match_score = 0
    print(f'\n')
    for file_name, file_info in sharepoint_files_dict.items():
        score = SequenceMatcher(None, bill_line_item_attachment_name, file_name).ratio()
        if score > best_match_score:
            best_match_score = score
            best_match = file_info
            print(f"Best match score: {best_match_score}")

    # Print the file name at the end before returning
    if best_match is not None:
        print(f"\nMatched file name: {best_match['file_name']}")
        return best_match['file']
    else:
        print(f"\nNo match found")
        return None


def _verify_file_content_match(site_id, item_id, access_token, bill_line_item_attachment):
    """
    Download SharePoint file and compare binary content with attachment
    """
    try:
        file_content = ms_drive_int.get_item(
            site_id=site_id,
            item_id=item_id,
            access_token=access_token
        )

        if file_content is None:
            print(f'\nNo file content found')
            return False

        attachment_content = bill_line_item_attachment.content

        # TODO: Use SHA256 instead of MD5, if we need to be more secure
        file_hash = hashlib.md5(file_content).hexdigest()
        attachment_hash = hashlib.md5(attachment_content).hexdigest()

        if len(file_content) == len(attachment_content) and file_hash == attachment_hash:
            return True
        else:
            return False

    except Exception as e:
        print(f"Error downloading/verifying SharePoint file: {str(e)}")
        return False


def _process_map_attachment_sharepoint_file(bill_line_item_attachment, new_sharepoint_file):
    #print(f'\nBill Line Item Attachment: {bill_line_item_attachment}')
    #print(f'\nNew Sharepoint File: {new_sharepoint_file}')
    read_map_attachment_sharepoint_file_resp = pers_map_attachment_sharepoint_file.\
        read_map_attachment_sharepoint_file_by_attachment_id_file_id(
            bill_line_item_attachment_id=bill_line_item_attachment.id,
            ms_sharepoint_file_id=new_sharepoint_file.file_id
        )
    if read_map_attachment_sharepoint_file_resp.success:
        map_attachment_sharepoint_file = read_map_attachment_sharepoint_file_resp.data[0]
        update_map_attachment_sharepoint_file_resp = pers_map_attachment_sharepoint_file.\
            update_map_attachment_sharepoint_file(
                map_attachment_sharepoint_file=map_attachment_sharepoint_file
            )
        if update_map_attachment_sharepoint_file_resp.success:
            return True
    else:
        create_map_attachment_sharepoint_file_resp = pers_map_attachment_sharepoint_file.\
            create_map_attachment_sharepoint_file(
                bill_line_item_attachment_id=bill_line_item_attachment.id,
                ms_sharepoint_file_id=new_sharepoint_file.file_id
            )
        if create_map_attachment_sharepoint_file_resp.success:
            return True
    return False


def main_sharepoint_sync_function():
    # Get bill line item attachments that are not mapped to a sharepoint file
    bill_line_item_attachments_not_mapped = None

    bill_line_item_attachments = _get_bill_line_item_attachments()
    #print(f'\nBill Line Item Attachments Count: {len(bill_line_item_attachments)}')
    if not bill_line_item_attachments:
        return 'No bill line item attachments found'

    bill_line_item_attachments_mapping = _get_bill_line_item_attachments_mapping()
    #print(f'\nBill Line Item Attachments Mapping Count: {len(bill_line_item_attachments_mapping)}')
    if not bill_line_item_attachments_mapping:
        bill_line_item_attachments_not_mapped = bill_line_item_attachments
    else:
        bill_line_item_attachments_not_mapped = _get_bill_line_item_attachments_not_mapped(
            attachments=bill_line_item_attachments,
            mapping=bill_line_item_attachments_mapping
        )


    # Get SharePoint site
    sharepoint_site = _get_sharepoint_site()
    if not sharepoint_site:
        return 'No SharePoint site found'

    # Refresh SharePoint access token
    api_ms_auth.refresh_token()

    # Get SharePoint access token
    access_token = _get_sharepoint_access_token()
    if not access_token:
        return 'No SharePoint access token found'

    # For each bill line item attachment, get bill line item
    bill_line_item = None
    sharepoint_folder = None
    sharepoint_files = None

    for bill_line_item_attachment in bill_line_item_attachments_not_mapped:
        #print(f'\nBill Line Item Attachment Name: {bill_line_item_attachment.name}')
    
        bill_line_item = _get_bill_line_item_by_bill_line_item_id(
            bill_line_item_id=bill_line_item_attachment.bill_line_item_id
        )
        #print(f'\nBill Line Item: {bill_line_item}')




        # For each bill line item attachment, get the SharePoint folder for that project.
        project_sharepoint_folder_mapping = _get_project_integration_sharepoint_folder_mapping(
            project_id=bill_line_item.project_id
        )
        #print(f'\nProject Sharepoint Folder Mapping: {project_sharepoint_folder_mapping}')

        if project_sharepoint_folder_mapping:
            sharepoint_folder = _get_sharepoint_folder_by_folder_id(
                folder_id=project_sharepoint_folder_mapping[0].ms_sharepoint_folder_id
            )
        #print(f'\nSharepoint Folder: {sharepoint_folder}')




        # For each bill line item attachment, get the SharePoint files for that project.
        sharepoint_files = _get_sharepoint_files(
            site_id=sharepoint_site.site_sharepoint_id,
            folder_id=sharepoint_folder.folder_ms_id,
            access_token=access_token
        )
        #print(f'\nSharepoint Files: {sharepoint_files[0]}')




        # Match the bill line item attachment to the SharePoint file
        matched_sharepoint_file = _match_sharepoint_file_to_bill_line_item_attachment(
            sharepoint_files=sharepoint_files,
            bill_line_item_attachment=bill_line_item_attachment
        )
        #print(f'\nMatched Sharepoint File: {matched_sharepoint_file}')

        # Verify the file content matches the bill line item attachment
        file_content_match = _verify_file_content_match(
            site_id=sharepoint_site.site_sharepoint_id,
            item_id=matched_sharepoint_file.get('id'),
            access_token=access_token,
            bill_line_item_attachment=bill_line_item_attachment
        )
        print(f'\nFirst Verify File Content Match: {file_content_match}')

        
        # If no matched sharepoint file, upload new file to SharePoint
        if not file_content_match:
            print('\nNo matched sharepoint file found - uploading new file')

            file_dict = [{
                'name': bill_line_item_attachment.name,
                'size': bill_line_item_attachment.size,
                'type': bill_line_item_attachment.type,
                'data': bill_line_item_attachment.content
            }]

            # Upload new file to SharePoint
            upload_new_file_result = ms_upload_new_file.upload_file_to_sharepoint(
                access_token=access_token,
                site_id=sharepoint_site.site_sharepoint_id,
                folder_id=sharepoint_folder.folder_ms_id,
                file=file_dict
            )
            if upload_new_file_result['success']:
                print(f'\nSuccessfully uploaded new file to SharePoint: {upload_new_file_result}')
                file_content_match = True
            else:
                print(f'\nFailed to upload new file to SharePoint: {upload_new_file_result}')





        

        if file_content_match:
            new_sharepoint_file = pers_ms_sharepoint_file.SharePointFile(
                file_ms_graph_download_url=matched_sharepoint_file.get('msGraphDownloadUrl'),
                file_c_tag=matched_sharepoint_file.get('cTag'),
                file_ms_created_datetime=matched_sharepoint_file.get('msCreatedDatetime'),
                file_e_tag=matched_sharepoint_file.get('eTag'),
                file_hash_quick_h_or_hash=matched_sharepoint_file.get('hashQuickHash'),
                file_mime_type=matched_sharepoint_file.get('mimeType'),
                file_ms_id=matched_sharepoint_file.get('id'),
                file_last_modified_datetime=matched_sharepoint_file.get('lastModifiedDatetime'),
                file_name=matched_sharepoint_file.get('name'),
                file_ms_parent_id=matched_sharepoint_file.get('parentId'),
                file_shared_scope=matched_sharepoint_file.get('sharedScope'),
                file_size=matched_sharepoint_file.get('size'),
                file_web_url=matched_sharepoint_file.get('webUrl')
            )
            #print(f'\nNew Sharepoint File: {new_sharepoint_file}')

            read_sharepoint_file_by_ms_id_resp = pers_ms_sharepoint_file.read_sharepoint_file_by_ms_id(
                ms_id=new_sharepoint_file.file_ms_id
            )
            #print(f'\nRead Sharepoint File By Ms Id Response: {read_sharepoint_file_by_ms_id_resp.message}')

            if read_sharepoint_file_by_ms_id_resp.success:
                new_sharepoint_file.file_id = read_sharepoint_file_by_ms_id_resp.data.file_id
                new_sharepoint_file.file_guid = read_sharepoint_file_by_ms_id_resp.data.file_guid
                new_sharepoint_file.file_created_datetime = read_sharepoint_file_by_ms_id_resp.data.file_created_datetime
                update_sharepoint_file_resp = pers_ms_sharepoint_file.update_sharepoint_file(
                    sharepoint_file=new_sharepoint_file
                )
                if update_sharepoint_file_resp.success:
                    if _process_map_attachment_sharepoint_file(
                        bill_line_item_attachment=bill_line_item_attachment,
                        new_sharepoint_file=new_sharepoint_file
                    ):
                        print('Map Attachment Sharepoint File Created')
                    else:
                        print('Failed to create Map Attachment Sharepoint File')

            else:
                create_sharepoint_file_resp = pers_ms_sharepoint_file.create_sharepoint_file(
                    sharepoint_file=new_sharepoint_file
                )
                if create_sharepoint_file_resp.success:
                    read_sharepoint_file_by_ms_id_resp = pers_ms_sharepoint_file.read_sharepoint_file_by_ms_id(
                        ms_id=new_sharepoint_file.file_ms_id
                    )
                    #print(f'\nRead Sharepoint File By Ms Id Response: {read_sharepoint_file_by_ms_id_resp.message}')

                    if read_sharepoint_file_by_ms_id_resp.success:
                        new_sharepoint_file.file_id = read_sharepoint_file_by_ms_id_resp.data.file_id
                        new_sharepoint_file.file_guid = read_sharepoint_file_by_ms_id_resp.data.file_guid
                        new_sharepoint_file.file_created_datetime = read_sharepoint_file_by_ms_id_resp.data.file_created_datetime
                        update_sharepoint_file_resp = pers_ms_sharepoint_file.update_sharepoint_file(
                            sharepoint_file=new_sharepoint_file
                        )
                        if update_sharepoint_file_resp.success:
                            if _process_map_attachment_sharepoint_file(
                                bill_line_item_attachment=bill_line_item_attachment,
                                new_sharepoint_file=new_sharepoint_file
                            ):
                                print('Map Attachment Sharepoint File Created')
                            else:
                                print('Failed to create Map Attachment Sharepoint File')
                else:
                    print('Failed to update SharePoint file')

    return 'Main Function Complete'


if __name__ == '__main__':
    print('KICKING OFF')
    main_result = main_sharepoint_sync_function()
    print(main_result)
