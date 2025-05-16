"""
Module to manage business processes for saving entry data to Buildone.
"""

import base64
import json
import os
import pathlib
import re
import requests

from datetime import datetime
from helper import function_help as hp
from integrations import ms_upload_new_file
from modules.project import pers_project
from modules.sub_cost_code import pers_sub_cost_code
from persistence import (
    pers_ms_sharepoint_folder,
    pers_ms_sharepoint_site,
    pers_ms_sharepoint_worksheet,
    pers_project_folder
)
from persistence.pers_response import SuccessResponse, PersistenceResponse, DatabaseError


def ensure_directory_exists(directory):
    if not os.path.exists(directory):
        os.makedirs(directory)


def save_json_file(content, filepath):
    #print(type(content))
    #print(filepath)
    ensure_directory_exists(os.path.dirname(filepath))
    with open(filepath, 'w', encoding='utf-8') as file:
        data = json.loads(content) if isinstance(content, str) else content
        json.dump(data, file, indent=4, ensure_ascii=False)


def get_workbook_worksheet_used_range(total_path, url, secrets):

    site_id = ''
    pers_ms_sharepoint_site_resp = pers_ms_sharepoint_site.read_sharepoint_sites()
    if isinstance(pers_ms_sharepoint_site_resp, SuccessResponse):
        site = pers_ms_sharepoint_site_resp.data[0]
        site_id = site.site_sharepoint_id
    #print(f'site_id: {site_id}')

    item_id = ''
    item_pattern = r"items\('([^']+)'\)"
    match = re.search(item_pattern, url)
    item_id = match.group(1)
    #print(item_id)

    worksheet_id = ''
    pers_ms_sharepoint_worksheet_resp = pers_ms_sharepoint_worksheet.\
        read_sharepoint_worksheets()
    if isinstance(pers_ms_sharepoint_worksheet_resp, SuccessResponse):
        worksheet = pers_ms_sharepoint_worksheet_resp.data[0]
        worksheet_id = worksheet.worksheet_ms_id
    #print(worksheet_id)

    access_token = secrets['ms']['access_token']
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Workbook-Session-Id': secrets[item_id]['id']
    }
    url = f'https://graph.microsoft.com/v1.0/sites/{site_id}/drive/items/{item_id}/workbook/worksheets/{worksheet_id}/usedRange'
    resp = requests.get(url=url, headers=headers, timeout=10)
    #print(resp.json())
    if resp.json().get('error'):
        print(resp.json())

    save_json_file(
        json.dumps(resp.json()),
        os.path.join(total_path, 'detail.json')
    )

    return resp.json()


def insert_ms_budget_tracker_record(site_root, secrets_url, line_items):
    """
    """
    secrets = hp.read_profile_secrets(secrets_url)
    access_token = secrets.get('ms').get('access_token')
    #print(access_token)

    for line_item in line_items:
        #print(line_item)    

        sub_cost_code_resp = pers_sub_cost_code.\
            read_buildone_sub_cost_code_by_guid(line_item.get('subCostCode'))
        if isinstance(sub_cost_code_resp, SuccessResponse):
            sub_cost_code = sub_cost_code_resp.data
            #print(sub_cost_code)

        project_id = 0
        project_resp = pers_project.\
            read_buildone_project_by_guid(line_item.get('project'))
        if isinstance(project_resp, SuccessResponse):
            project = project_resp.data
            project_id = project.project_id
            #print(project)

        project_url = ''
        project_path = ''
        project_folder_resp = pers_project_folder.\
            read_buildone_project_folders_by_project_id_by_module(
                project_id,
                'worksheet'
            )
        if isinstance(project_folder_resp, SuccessResponse):
            project_folder = project_folder_resp.data
            project_url = project_folder.url
            project_path = project_folder.path
            #print(project_folder)

            #print(project_url)

            raw_root = r"{}".format(site_root)
            #print(f'raw_root: {raw_root}')
            path = project_path.lstrip('\\')
            #print(f'path: {path}')
            total_path = os.path.normpath(os.path.join(raw_root, path))
            #print(f'total_path: {total_path}')

            used_range = get_workbook_worksheet_used_range(
                total_path=total_path,
                url=project_url,
                secrets=secrets
            )
            column_count = used_range.get('columnCount')
            print(f'column_count: {column_count}')
            column_index = used_range.get('columnIndex')
            print(f'column_index: {column_index}')
            row_count = used_range.get('rowCount')
            print(f'row_count: {row_count}')
            row_index = used_range.get('rowIndex')
            print(f'row_index: {row_index}')
            row_values = used_range.get('values')

            last_matching_index = -1
            for i, row in enumerate(row_values):
                if row[0] == 8:
                    if row[1] == 8.01:
                        if row[7] != '':
                            #print(f'Row: {i}')
                            #print(row)
                            last_matching_index = i + 1 if i != -1 else -1

                            #print(f'last_matching_index: {last_matching_index}')

            start_column = chr(ord('A') + column_index)
            end_column = chr(ord('A') + column_index + column_count - 1)
            row = last_matching_index + 1
            #print(f'start_column: {start_column}')
            #print(f'end_column: {end_column}')
            #print(f'row: {row}')
            print(f'Insert Range: {start_column}{row}:{end_column}{row}')

    return {
        'status_code': 404,
        'message': 'File saved successfully'
    }
