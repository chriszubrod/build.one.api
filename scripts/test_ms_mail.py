# Python Standard Library Imports
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Local Imports
from integrations.ms.mail.external.client import (
    list_mail_folders,
    list_messages,
    get_message,
    list_message_attachments,
)

if __name__ == "__main__":
    print("\n" + "=" * 80)
    print("TESTING MICROSOFT GRAPH MAIL API")
    print("=" * 80)
    
    # Test 1: List Mail Folders
    print("\n📁 Test 1: Mail Folders")
    print("-" * 40)
    
    folders_result = list_mail_folders()
    
    if folders_result.get("status_code") == 200:
        print(f"✅ SUCCESS! Found {len(folders_result.get('folders', []))} folders")
        for folder in folders_result.get("folders", []):
            unread = folder.get("unread_item_count", 0)
            total = folder.get("total_item_count", 0)
            print(f"   - {folder.get('display_name')}: {total} items ({unread} unread)")
    else:
        print(f"❌ FAILED: {folders_result.get('message')}")
    
    # Test 2: List Inbox Messages
    print("\n📬 Test 2: Inbox Messages (top 5)")
    print("-" * 40)
    
    messages_result = list_messages(folder="inbox", top=5)
    
    if messages_result.get("status_code") == 200:
        print(f"✅ SUCCESS! Retrieved {len(messages_result.get('messages', []))} messages")
        print(f"   Total in folder: {messages_result.get('total_count', 'unknown')}")
        
        for i, msg in enumerate(messages_result.get("messages", []), 1):
            read_status = "✓" if msg.get("is_read") else "●"
            attachments = "📎" if msg.get("has_attachments") else ""
            print(f"\n   [{i}] {read_status} {msg.get('subject', 'No Subject')} {attachments}")
            print(f"       From: {msg.get('from_name')} <{msg.get('from_email')}>")
            print(f"       Date: {msg.get('received_datetime')}")
            
            # Store first message ID for next test
            if i == 1:
                first_message_id = msg.get("message_id")
    else:
        print(f"❌ FAILED: {messages_result.get('message')}")
        first_message_id = None
    
    # Test 3: Get Full Message
    if first_message_id:
        print("\n📧 Test 3: Full Message Details")
        print("-" * 40)
        
        message_result = get_message(first_message_id, include_body=True)
        
        if message_result.get("status_code") == 200:
            email = message_result.get("email", {})
            print(f"✅ SUCCESS!")
            print(f"   Subject: {email.get('subject')}")
            print(f"   From: {email.get('from_name')} <{email.get('from_email')}>")
            print(f"   To: {[r.get('email') for r in email.get('to_recipients', [])]}")
            print(f"   Conversation ID: {email.get('conversation_id', 'N/A')[:30]}...")
            print(f"   Has Body: {'Yes' if email.get('body_content') else 'No'}")
            print(f"   Body Type: {email.get('body_content_type')}")
            body_preview = (email.get('body_preview') or '')[:100]
            print(f"   Preview: {body_preview}...")
        else:
            print(f"❌ FAILED: {message_result.get('message')}")
    
    # Test 4: List Attachments
    if first_message_id:
        print("\n📎 Test 4: Message Attachments")
        print("-" * 40)
        
        attachments_result = list_message_attachments(first_message_id)
        
        if attachments_result.get("status_code") == 200:
            attachments = attachments_result.get("attachments", [])
            if attachments:
                print(f"✅ SUCCESS! Found {len(attachments)} attachments")
                for att in attachments:
                    size_kb = (att.get("size") or 0) / 1024
                    print(f"   - {att.get('name')} ({size_kb:.1f} KB, {att.get('content_type')})")
            else:
                print("✅ SUCCESS! No attachments on this message")
        else:
            print(f"❌ FAILED: {attachments_result.get('message')}")
    
    # Test 5: Search Messages
    print("\n🔍 Test 5: Search Messages")
    print("-" * 40)
    
    search_result = list_messages(folder="inbox", top=3, search="invoice")
    
    if search_result.get("status_code") == 200:
        results = search_result.get("messages", [])
        print(f"✅ SUCCESS! Found {len(results)} messages matching 'invoice'")
        for msg in results:
            print(f"   - {msg.get('subject', 'No Subject')}")
    else:
        print(f"❌ FAILED: {search_result.get('message')}")
    
    # Test 6: Filter Unread Messages
    print("\n📩 Test 6: Unread Messages")
    print("-" * 40)
    
    unread_result = list_messages(folder="inbox", top=3, filter_query="isRead eq false")
    
    if unread_result.get("status_code") == 200:
        results = unread_result.get("messages", [])
        print(f"✅ SUCCESS! Found {len(results)} unread messages (showing up to 3)")
        for msg in results:
            print(f"   - {msg.get('subject', 'No Subject')}")
    else:
        print(f"❌ FAILED: {unread_result.get('message')}")
    
    print("\n" + "=" * 80)
    print("TEST COMPLETE")
    print("=" * 80 + "\n")
