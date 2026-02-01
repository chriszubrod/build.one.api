#!/usr/bin/env python3
"""
Test script for Phase 7: AI Copilot

Tests:
1. Intent detection
2. Conversation flow
3. Service integration
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.copilot.business.service import get_copilot_service
from services.copilot.business.model import CopilotIntent


def main():
    print("=" * 60)
    print("Phase 7: AI Copilot Test")
    print("=" * 60)
    print()

    copilot = get_copilot_service()

    # Test 1: Greeting
    print("1. Testing greeting...")
    response = copilot.chat("Hello!")
    print(f"   Intent: {response.intent}")
    print(f"   Response: {response.message[:200]}...")
    print(f"   Suggestions: {response.suggestions}")
    print()

    # Test 2: Help request
    print("2. Testing help request...")
    response = copilot.chat("What can you help me with?")
    print(f"   Intent: {response.intent}")
    print(f"   Response length: {len(response.message)} chars")
    print()

    # Test 3: Status check
    print("3. Testing status check...")
    response = copilot.chat("What's the current status?")
    print(f"   Intent: {response.intent}")
    print(f"   Response: {response.message[:300]}...")
    if response.data:
        print(f"   Data: {response.data}")
    print()

    # Test 4: Search request
    print("4. Testing document search...")
    response = copilot.chat("Find documents about lumber")
    print(f"   Intent: {response.intent}")
    print(f"   Action taken: {response.action_taken}")
    print(f"   Response: {response.message[:200]}...")
    print()

    # Test 5: Question answering
    print("5. Testing question answering...")
    response = copilot.chat("What invoices do we have?")
    print(f"   Intent: {response.intent}")
    print(f"   Response: {response.message[:200]}...")
    if response.sources:
        print(f"   Sources: {len(response.sources)} documents")
    print()

    # Test 6: Multi-turn conversation
    print("6. Testing multi-turn conversation...")
    conv_id = None
    
    # First message
    response1 = copilot.chat("I need help with bills")
    conv_id = list(copilot._conversations.keys())[-1]
    print(f"   Turn 1: {response1.message[:100]}...")
    print(f"   Conversation ID: {conv_id}")
    
    # Second message in same conversation
    response2 = copilot.chat("Show me recent ones", conversation_id=conv_id)
    print(f"   Turn 2: {response2.message[:100]}...")
    
    # Check conversation has both messages
    conversation = copilot.get_conversation(conv_id)
    print(f"   Messages in conversation: {len(conversation.messages)}")
    print()

    # Test 7: Intent detection accuracy
    print("7. Testing intent detection...")
    test_cases = [
        ("Find all bills from Home Depot", CopilotIntent.SEARCH_DOCUMENTS),
        ("What's pending review?", CopilotIntent.GET_STATUS),
        ("Categorize document abc123", CopilotIntent.CATEGORIZE_DOCUMENT),
        ("Check for duplicates", CopilotIntent.CHECK_DUPLICATES),
        ("Hi there!", CopilotIntent.GREETING),
    ]
    
    for message, expected_intent in test_cases:
        response = copilot.chat(message)
        match = "✓" if response.intent == expected_intent else "✗"
        print(f"   {match} '{message[:30]}...' -> {response.intent}")
    print()

    print("=" * 60)
    print("Phase 7 AI Copilot Test Complete!")
    print("=" * 60)
    print()
    print("API Endpoints available:")
    print("  POST /api/v1/copilot/chat                    - Send message")
    print("  GET  /api/v1/copilot/conversations/{id}      - Get conversation")
    print("  DELETE /api/v1/copilot/conversations/{id}    - Delete conversation")
    print("  GET  /api/v1/copilot/quick-actions           - Get quick actions")

    return 0


if __name__ == "__main__":
    sys.exit(main())
