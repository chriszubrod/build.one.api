# Python Standard Library Imports
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Local Imports
from integrations.ms.auth.external.client import test_ms_graph_connection, get_ms_graph_messages

if __name__ == "__main__":
    print("\n" + "=" * 80)
    print("TESTING MICROSOFT GRAPH API")
    print("=" * 80)
    
    # Test 1: User Profile
    print("\n📧 Test 1: User Profile (/me)")
    print("-" * 40)
    
    result = test_ms_graph_connection()
    
    if result.get("status_code") == 200:
        print("✅ SUCCESS!")
        user = result.get("user", {})
        print(f"   Display Name: {user.get('display_name')}")
        print(f"   Email: {user.get('email')}")
        print(f"   User ID: {user.get('id')}")
    else:
        print(f"❌ FAILED: {result.get('message')}")
    
    # Test 2: Email Messages
    print("\n📬 Test 2: Recent Emails (inbox, top 5)")
    print("-" * 40)
    
    email_result = get_ms_graph_messages(top=5, folder="inbox")
    
    if email_result.get("status_code") == 200:
        print(f"✅ SUCCESS! Retrieved {email_result.get('count')} messages")
        messages = email_result.get("messages", [])
        for i, msg in enumerate(messages, 1):
            print(f"\n   [{i}] {msg.get('subject', 'No Subject')}")
            print(f"       From: {msg.get('from_name')} <{msg.get('from_email')}>")
            print(f"       Date: {msg.get('received')}")
            print(f"       Read: {'Yes' if msg.get('is_read') else 'No'}, Attachments: {'Yes' if msg.get('has_attachments') else 'No'}")
    else:
        print(f"❌ FAILED: {email_result.get('message')}")
    
    print("\n" + "=" * 80 + "\n")
