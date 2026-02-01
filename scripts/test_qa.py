#!/usr/bin/env python3
"""
Test script for Phase 4: Natural Language Queries

Tests the Q&A service with sample questions to verify:
1. Question analysis works
2. Document search integration works
3. GPT-4o-mini answer generation works
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.qa.business.service import get_qa_service


def main():
    print("=" * 60)
    print("Phase 4: Natural Language Queries Test")
    print("=" * 60)
    print()

    qa_service = get_qa_service()

    # Test questions
    test_questions = [
        "What does this document say?",
        "Tell me about the content in my documents",
        "What kind of information is stored here?",
    ]

    # Step 1: Test question analysis
    print("1. Testing question analysis...")
    sample_question = "What invoices were submitted last month?"
    try:
        analysis = qa_service.analyze_question(sample_question)
        print(f"   Question: {sample_question}")
        print(f"   Intent: {analysis.get('intent', 'unknown')}")
        print(f"   Entities: {analysis.get('entities', [])}")
        print(f"   Filters: {analysis.get('filters', {})}")
        print(f"   Search Query: {analysis.get('search_query', '')}")
    except Exception as e:
        print(f"   Error: {e}")

    # Step 2: Test Q&A with the test document
    print("\n2. Testing Q&A with indexed documents...")
    for question in test_questions:
        print(f"\n   Question: \"{question}\"")
        print("   " + "-" * 50)
        
        try:
            result = qa_service.ask(
                question=question,
                max_documents=3,
                search_mode="hybrid",
            )
            
            # Print answer
            answer = result.get("answer", "")
            print(f"   Answer: {answer[:500]}...")
            if len(answer) > 500:
                print("   [truncated]")
            
            # Print sources
            sources = result.get("sources", [])
            if sources:
                print(f"\n   Sources ({len(sources)}):")
                for src in sources[:3]:
                    print(f"   - {src.get('filename')} (score: {src.get('score', 0):.4f})")
            else:
                print("\n   No sources found")
                
        except Exception as e:
            print(f"   Error: {e}")
            import traceback
            traceback.print_exc()

    # Step 3: Test with a specific query about our test document
    print("\n3. Testing specific query about test document...")
    specific_question = "What does the Hello World document contain?"
    print(f"   Question: \"{specific_question}\"")
    print("   " + "-" * 50)
    
    try:
        result = qa_service.ask(
            question=specific_question,
            max_documents=5,
            search_mode="semantic",
        )
        
        answer = result.get("answer", "")
        print(f"   Answer: {answer}")
        
        sources = result.get("sources", [])
        if sources:
            print(f"\n   Sources ({len(sources)}):")
            for src in sources:
                print(f"   - {src.get('filename')}")
                
    except Exception as e:
        print(f"   Error: {e}")

    print()
    print("=" * 60)
    print("Phase 4 Q&A Test Complete!")
    print("=" * 60)
    print()
    print("API Endpoints available:")
    print("  POST /api/v1/qa/ask      - Ask a question (JSON body)")
    print("  GET  /api/v1/qa/ask?q=   - Ask a question (query param)")
    print("  POST /api/v1/qa/analyze  - Analyze question intent")

    return 0


if __name__ == "__main__":
    sys.exit(main())
