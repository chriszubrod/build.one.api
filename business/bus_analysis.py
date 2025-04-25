"""
This module contains the business logic for the analysis API.
"""
# python standard library imports
import json
import time
from datetime import datetime

# third party imports
import requests

# local imports
from business.bus_response import BusinessResponse





def pass_chat_to_slm_route(chat_message: str):
    """
    Passes a chat message to SLM for analysis.
    """
    print("Chat message is being passed to SLM.")

    try:
        prompt = (
            f"""{chat_message}"""
        )

        print(f'Prompt: {prompt}')
        url = 'http://localhost:11434/api/generate'
        headers = {
            'Content-Type': 'application/json'
        }
        data = {
            'model': 'llama3.2:3b',
            'prompt': prompt,
            'format': '',
            'stream': False
        }

        print("Sending request to SLM.")
        start_time = time.time()
        slm = requests.post(
            url=url,
            data=json.dumps(data),
            headers=headers,
            timeout=60
        )
        print("Received response from SLM.")
        print(slm.json())
        if slm.json().get('response'):
            slm_response = slm.json().get('response')
            print(slm_response)
            print(f'Elapsed time: {time.time() - start_time}')

            return BusinessResponse(
                data=slm_response,
                success=True,
                status_code=200,
                message="Chat message passed to SLM.",
                timestamp=datetime.now()
            )

    except Exception as e:
        print(f'Error: {e}')
        return BusinessResponse(
            data=None,
            success=False,
            status_code=500,
            message=f"Error passing chat to SLM: {e}",
            timestamp=datetime.now()
        )
