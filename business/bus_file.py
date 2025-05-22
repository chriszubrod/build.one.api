"""
Module to manage business processes for saving entry data to Buildone.
"""

import base64
import os
import pathlib

from business.bus_business_responses import BusinessResponse
from datetime import datetime
from helper import function_help as hp
from integrations.ms import ms_upload_new_file
from modules.project import pers_project
from persistence import (
    pers_ms_sharepoint_folder,
    pers_ms_sharepoint_site,
    pers_project_folder
)
from persistence.pers_response import SuccessResponse, PersistenceResponse, DatabaseError


def ensure_directory_exists(directory):
    if not os.path.exists(directory):
        os.makedirs(directory)


def save_txt_file(content, filepath):
    txt_filepath = filepath.replace('.pdf', '.txt')
    ensure_directory_exists(os.path.dirname(txt_filepath))
    with open(txt_filepath, 'w', encoding='utf-8') as file:
        result = file.write(content)

        print(f'Save Text File Result: {filepath}, Bytes Written: {result}')
        return result


def save_pdf_file(pdf_content, filepath):
    print(f'Save PDF File - Filepath: {filepath}')
    ensure_directory_exists(os.path.dirname(filepath))
    with open(filepath, 'wb') as file:
        base64_data = pdf_content.split('base64,')[1]
        pdf_bytes = base64.b64decode(base64_data)
        result = file.write(pdf_bytes)
        print(f'Save PDF File Result: {filepath}, Bytes Written: {result}')
        return result


def save_buildone_file(site_root, files, file_text, line_items):
    file = files[0]
    path = ''
    for line_item in line_items:
        project_guid = line_item.get('project')
        pers_buildone_project_resp = pers_project.\
            read_buildone_project_by_guid(project_guid)
        if isinstance(pers_buildone_project_resp, SuccessResponse):
            project_id = pers_buildone_project_resp.data.project_id

            pers_buildone_project_folder_resp = pers_project_folder.\
                read_buildone_project_folders_by_project_id_by_module(project_id, 'bill')
            if isinstance(pers_buildone_project_folder_resp, SuccessResponse):
                project_folder = pers_buildone_project_folder_resp.data
                print(f'Project folder: {project_folder}')

                raw_root = r"{}".format(site_root)
                path = project_folder.path.lstrip('\\')
                total_path = os.path.normpath(os.path.join(raw_root, path))

                save_pdf_file_result = save_pdf_file(
                    file.get('data'),
                    os.path.join(total_path, f'{file.get("name")}')
                )

                save_txt_file_result = save_txt_file(
                    file_text,
                    os.path.join(total_path, f'{file.get("name")}')
                )

                if save_pdf_file_result > 0:
                    return BusinessResponse(
                        success=True,
                        status_code=200,
                        message='File saved successfully'
                    )

                return BusinessResponse(
                    success=False,
                    status_code=400,
                    message='File not saved'
                )

            return BusinessResponse(
                success=False,
                status_code=400,
                message='Project folder not found'
            )


        return BusinessResponse(
            success=False,
            status_code=400,
            message='Project not found'
        )



def post_buildone_file(secrets_url, file, line_items):
    """
    """
    secrets = hp.read_profile_secrets(secrets_url)
    access_token = secrets.get('ms').get('access_token')

    pers_sharepoint_sites_resp = pers_ms_sharepoint_site.read_sharepoint_sites()
    if isinstance(pers_sharepoint_sites_resp, SuccessResponse):
        pers_sharepoint_sites = pers_sharepoint_sites_resp.data
        for pers_sharepoint_site in pers_sharepoint_sites:
            site_id = pers_sharepoint_site.site_sharepoint_id

    for line_item in line_items:
        project_guid = line_item.get('project')
        pers_buildone_project_resp = pers_project.\
            read_buildone_project_by_guid(project_guid)
        if isinstance(pers_buildone_project_resp, SuccessResponse):
            project_id = pers_buildone_project_resp.data.project_id

            pers_buildone_project_folder_resp = pers_project_folder.\
                read_buildone_project_folders_by_project_id(project_id)
            if isinstance(pers_buildone_project_folder_resp, SuccessResponse):
                project_folders = pers_buildone_project_folder_resp.data
                for project_folder in project_folders:
                    if project_folder.module == 'bill':
                        url = project_folder.url

                        pers_ms_sharepoint_folder_resp = pers_ms_sharepoint_folder.\
                            read_sharepoint_folder_by_url(url)

                        if isinstance(pers_ms_sharepoint_folder_resp, SuccessResponse):
                            folder_id = pers_ms_sharepoint_folder_resp.data.folder_ms_id

        ms_upload_new_file_resp = ms_upload_new_file.upload_file_to_sharepoint(
            access_token,
            site_id,
            folder_id,
            file
        )

        return {
            'status_code': 404,
            'message': ms_upload_new_file_resp
        }
