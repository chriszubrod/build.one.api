from helper import url_help
import requests


def build_vendor_count_uri(urls, realm_id, after_datetime):
    """Build uri to query vendor count."""
    query = (
        f"select count(*) from Vendor Where Metadata.LastUpdatedTime > '{after_datetime}'"
    )
    query_name = 'queryvendor'
    uri = url_help.build_uri(
        urls=urls,
        query=query,
        query_name=query_name,
        realm_id=realm_id
    )
    return uri


def build_vendor_uri(urls, realm_id, after_datetime, start_position=1, max_results=1000):
    """Build uri to query vendors."""
    query = (
        f"select * from Vendor Where Metadata.LastUpdatedTime > '{after_datetime}' "
        f"STARTPOSITION {start_position} MAXRESULTS {max_results}"
    )
    query_name = 'queryvendor'
    uri = url_help.build_uri(
        urls=urls,
        query=query,
        query_name=query_name,
        realm_id=realm_id
    )
    return uri


def query_intuit_vendor_count_info(urls, realm_id, last_update, access_token):
    """Query Intuit Vendor Count Info."""
    # build uri for vendor count, pass in url response message and pass realmid
    vendor_count_uri = build_vendor_count_uri(
        urls=urls,
        realm_id=realm_id,
        after_datetime=last_update
    )

    # try to request a response from the intuit vendor uri endpoint
    try:
        url = vendor_count_uri
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": "bearer " + access_token
        }
        resp = requests.get(url=url, headers=headers, timeout=10)
        return {
            "message": resp.text,
            "status_code": resp.status_code
        }
    except Exception as e:
        return {
            "message": f"An error occured while trying to call intuit vendor endpoint: {e}",
            "status_code": 500
        }


def query_intuit_vendor_info(urls, realm_id, last_update, access_token, start_position=1, max_results=1000):
    """Query Intuit Vendor Info."""
    # build uri for vendor, pass in url response message and pass realmid
    vendor_uri = build_vendor_uri(
        urls=urls,
        realm_id=realm_id,
        after_datetime=last_update,
        start_position=start_position,
        max_results=max_results
    )

    # try to request a response from the intuit vendor uri endpoint
    try:
        url = vendor_uri
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": "bearer " + access_token
        }
        resp = requests.get(url=url, headers=headers, timeout=10)
        return {
            "message": resp.text,
            "status_code": resp.status_code
        }
    except Exception as e:
        return {
            "message": f"An error occured while trying to call intuit vendor endpoint: {e}",
            "status_code": 500
        }
