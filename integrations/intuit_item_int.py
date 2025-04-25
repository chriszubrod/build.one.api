from helper import url_help
import requests


def build_item_count_uri(urls, realm_id, after_datetime):
    """Build uri to query item count."""
    query = (
        f"select count(*) from Item Where Metadata.LastUpdatedTime > '{after_datetime}'"
    )
    query_name = 'queryitem'
    uri = url_help.build_uri(
        urls=urls,
        query=query,
        query_name=query_name,
        realm_id=realm_id
    )
    return uri


def build_item_uri(urls, realm_id, after_datetime, start_position=1, max_results=1000):
    """Build uri to query items."""
    query = (
        f"select * from Item Where Metadata.LastUpdatedTime > '{after_datetime}' "
        f"STARTPOSITION {start_position} MAXRESULTS {max_results}"
    )
    query_name = 'queryitem'
    uri = url_help.build_uri(
        urls=urls,
        query=query,
        query_name=query_name,
        realm_id=realm_id
    )
    return uri


def query_intuit_item_count_info(urls, realm_id, last_update, access_token):
    """Query Intuit Item Count Info."""
    # build uri for item count, pass in url response message and pass realmid
    item_count_uri = build_item_count_uri(
        urls=urls,
        realm_id=realm_id,
        after_datetime=last_update
    )

    # try to request a response from the intuit item uri endpoint
    try:
        url = item_count_uri
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
            "message": f"An error occured while trying to call intuit item endpoint: {e}",
            "status_code": 500
        }


def query_intuit_item_info(urls, realm_id, last_update, access_token, start_position=1, max_results=1000):
    """Query Intuit Item Info."""
    # build uri for item, pass in url response message and pass realmid
    item_uri = build_item_uri(
        urls=urls,
        realm_id=realm_id,
        after_datetime=last_update,
        start_position=start_position,
        max_results=max_results
    )

    # try to request a response from the intuit item uri endpoint
    try:
        url = item_uri
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
            "message": f"An error occured while trying to call intuit item endpoint: {e}",
            "status_code": 500
        }


