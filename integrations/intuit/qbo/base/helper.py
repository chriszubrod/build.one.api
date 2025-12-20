# Python Standard Library Imports
import json
import requests

# Third-party Imports

# Local Imports


def get_intuit_discovery_document():
    try:
        url = "https://developer.api.intuit.com/.well-known/openid_configuration"
        headers = {
            "Accept": "application/json"
        }
        resp = requests.get(url=url, headers=headers)
        if resp.status_code == 400:
            return "An error occured during intuit discovery document. " + resp.text

        if resp.status_code == 200:
            return json.loads(resp.text)

    except:
        return "An error occured while trying to call openid production configuration."

