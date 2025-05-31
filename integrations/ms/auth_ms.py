# python standard library imports
import asyncio
import os
import json
import sys
from typing import Dict, Any

# Add project root to Python path
project_root = os.path.realpath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.append(project_root)

# Set secrets.json path
os.environ['SECRETS_PATH'] = os.path.join(project_root, 'secrets.json')

# third party imports
from azure.identity import DeviceCodeCredential
from msgraph import GraphServiceClient

# local imports
from utils.config_help import get_secrets, write_secrets


secrets = get_secrets()
if not secrets:
    raise Exception("Failed to load secrets")

ms_secrets = secrets['ms']

# Use DeviceCodeCredential for delegated permissions
credential = DeviceCodeCredential(
    tenant_id=ms_secrets['tenant'],
    client_id=ms_secrets['client_id']
)

# Use the specific scopes you have permission for
scopes = ['User.Read', 'Files.Read.All', 'Files.ReadWrite.All', 'Mail.Read', 
          'Sites.Read.All', 'Sites.Selected', 'profile', 'openid', 'email']

client = GraphServiceClient(credential, scopes)

# Get current user's info
async def get_user():
    try:
        user = await client.me.get()
        print(f"\nCurrent User:")
        print(f"Name: {user.display_name}")
        print(f"Email: {user.user_principal_name}")
        return user
    except Exception as e:
        print(f"Error getting user info: {str(e)}")
        return None

# List current user's drives
async def get_drives():
    try:
        drives = await client.me.drives.get()
        if drives and drives.value:
            print("\nYour Drives:")
            for drive in drives.value:
                print(f"Drive: {drive.name} ({drive.drive_type})")
                print(f"ID: {drive.id}")
                print(f"Web URL: {drive.web_url}")
                print("---")
            return drives
        return None
    except Exception as e:
        print(f"Error getting drives: {str(e)}")
        return None

async def main():
    print("Starting authentication...")
    print("You will need to authenticate in your browser.")
    print("The code will wait for you to complete the authentication.")
    
    # Get user info first
    print("\nFetching user info...")
    await get_user()
    
    # Then get drives
    print("\nFetching drives...")
    await get_drives()

# Run the async main function
if __name__ == "__main__":
    asyncio.run(main())
