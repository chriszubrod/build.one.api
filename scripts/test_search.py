#!/usr/bin/env python3
"""
Test script for Phase 3: Semantic Search

Tests the search service with:
1. Index the test attachment from Phase 2
2. Perform keyword search
3. Perform semantic search
4. Perform hybrid search
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.attachment.business.service import AttachmentService
from services.search.business.service import get_search_service


def main():
    print("=" * 60)
    print("Phase 3: Semantic Search Test")
    print("=" * 60)
    print()

    attachment_service = AttachmentService()
    search_service = get_search_service()

    # Step 1: Find a completed extraction to index
    print("1. Finding attachments with completed extraction...")
    attachments = attachment_service.read_all()
    completed = [a for a in attachments if a.extraction_status == "completed" and a.extracted_text_blob_url]
    
    if not completed:
        print("   No attachments with completed extraction found.")
        print("   Run test_extraction.py first to create one.")
        return 1
    
    print(f"   Found {len(completed)} attachments with completed extraction")
    
    # Step 2: Index the attachments
    print("\n2. Indexing attachments in Azure AI Search...")
    indexed_count = 0
    for attachment in completed[:5]:  # Index up to 5
        try:
            success = search_service.index_attachment(attachment)
            if success:
                print(f"   Indexed: {attachment.original_filename} (ID: {attachment.id})")
                indexed_count += 1
            else:
                print(f"   Failed: {attachment.original_filename}")
        except Exception as e:
            print(f"   Error indexing {attachment.original_filename}: {e}")
    
    print(f"   Indexed {indexed_count} attachments")
    
    if indexed_count == 0:
        print("   No attachments indexed. Cannot continue with search tests.")
        return 1

    # Give Azure Search a moment to process
    import time
    print("\n   Waiting for index to update...")
    time.sleep(2)

    # Step 3: Test keyword search
    print("\n3. Testing keyword search...")
    query = "Hello"
    try:
        results = search_service.search(query=query, top=5)
        print(f"   Query: '{query}'")
        print(f"   Results: {len(results)}")
        for r in results:
            print(f"   - {r['filename']}: score={r['score']:.4f}")
            if r.get('content_preview'):
                preview = r['content_preview'][:100]
                print(f"     Preview: {preview}...")
    except Exception as e:
        print(f"   Error: {e}")

    # Step 4: Test semantic search
    print("\n4. Testing semantic search...")
    query = "greeting message"  # Semantically similar to "Hello World"
    try:
        results = search_service.semantic_search(query=query, top=5)
        print(f"   Query: '{query}'")
        print(f"   Results: {len(results)}")
        for r in results:
            print(f"   - {r['filename']}: score={r['score']:.4f}")
            if r.get('content_preview'):
                preview = r['content_preview'][:100]
                print(f"     Preview: {preview}...")
    except Exception as e:
        print(f"   Error: {e}")

    # Step 5: Test hybrid search
    print("\n5. Testing hybrid search...")
    query = "Hello World greeting"
    try:
        results = search_service.hybrid_search(query=query, top=5)
        print(f"   Query: '{query}'")
        print(f"   Results: {len(results)}")
        for r in results:
            print(f"   - {r['filename']}: score={r['score']:.4f}")
            if r.get('content_preview'):
                preview = r['content_preview'][:100]
                print(f"     Preview: {preview}...")
    except Exception as e:
        print(f"   Error: {e}")

    print()
    print("=" * 60)
    print("Phase 3 Search Test Complete!")
    print("=" * 60)
    print()
    print("API Endpoints available:")
    print("  GET  /api/v1/search/documents?q=<query>&mode=hybrid")
    print("  POST /api/v1/search/index/{public_id}")
    print("  DELETE /api/v1/search/index/{public_id}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
