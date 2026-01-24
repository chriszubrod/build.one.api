#!/usr/bin/env python3
"""
Test script for Phase 5: Anomaly Detection

Tests:
1. Exact duplicate detection (file hash)
2. Near duplicate detection (semantic similarity)
3. Pre-upload check
"""

import sys
import os
import hashlib

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.anomaly.business.service import get_anomaly_service
from modules.attachment.business.service import AttachmentService


def main():
    print("=" * 60)
    print("Phase 5: Anomaly Detection Test")
    print("=" * 60)
    print()

    anomaly_service = get_anomaly_service()
    attachment_service = AttachmentService()

    # Step 1: Get an existing attachment to test
    print("1. Finding attachments to test...")
    attachments = attachment_service.read_all()
    
    if not attachments:
        print("   No attachments found. Run test_extraction.py first.")
        return 1
    
    print(f"   Found {len(attachments)} attachments")

    # Step 2: Test anomaly check on existing attachment
    print("\n2. Testing anomaly check on existing attachment...")
    test_attachment = attachments[0]
    print(f"   Checking: {test_attachment.original_filename} (ID: {test_attachment.id})")
    
    try:
        result = anomaly_service.check_attachment(test_attachment)
        print(f"   Has anomaly: {result.has_anomaly}")
        if result.has_anomaly:
            print(f"   Type: {result.anomaly_type}")
            print(f"   Severity: {result.severity}")
            print(f"   Blocked: {result.blocked}")
            print(f"   Flagged: {result.flagged}")
            print(f"   Message: {result.message}")
        else:
            print(f"   Message: {result.message}")
    except Exception as e:
        print(f"   Error: {e}")
        import traceback
        traceback.print_exc()

    # Step 3: Test exact duplicate detection
    print("\n3. Testing exact duplicate detection...")
    if test_attachment.file_hash:
        print(f"   Using hash: {test_attachment.file_hash[:16]}...")
        try:
            result = anomaly_service.check_for_duplicates_before_upload(
                file_hash=test_attachment.file_hash,
            )
            print(f"   Has anomaly: {result.has_anomaly}")
            if result.has_anomaly:
                print(f"   Type: {result.anomaly_type}")
                print(f"   Message: {result.message}")
                for doc in result.related_documents:
                    print(f"   - Related: {doc.filename} ({doc.match_reason})")
        except Exception as e:
            print(f"   Error: {e}")
    else:
        print("   Skipped: Test attachment has no file hash")

    # Step 4: Test semantic similarity detection
    print("\n4. Testing semantic similarity detection...")
    extracted_attachments = [a for a in attachments if a.extraction_status == "completed"]
    
    if extracted_attachments:
        test_extracted = extracted_attachments[0]
        print(f"   Using: {test_extracted.original_filename}")
        
        try:
            result = anomaly_service.check_attachment(
                test_extracted,
                check_category_only=False,
            )
            print(f"   Has anomaly: {result.has_anomaly}")
            if result.has_anomaly:
                print(f"   Type: {result.anomaly_type}")
                print(f"   Severity: {result.severity}")
                print(f"   Related documents: {len(result.related_documents)}")
                for doc in result.related_documents[:3]:
                    print(f"   - {doc.filename}: {doc.similarity_score:.2%} similar")
        except Exception as e:
            print(f"   Error: {e}")
            import traceback
            traceback.print_exc()
    else:
        print("   Skipped: No extracted attachments found")

    # Step 5: Test pre-upload check with new hash (should pass)
    print("\n5. Testing pre-upload check with new file...")
    new_hash = hashlib.sha256(b"completely unique content " + os.urandom(16)).hexdigest()
    print(f"   Testing with hash: {new_hash[:16]}...")
    
    try:
        result = anomaly_service.check_for_duplicates_before_upload(
            file_hash=new_hash,
        )
        print(f"   Has anomaly: {result.has_anomaly}")
        print(f"   Message: {result.message}")
        if not result.has_anomaly:
            print("   ✓ New file would be allowed")
    except Exception as e:
        print(f"   Error: {e}")

    print()
    print("=" * 60)
    print("Phase 5 Anomaly Detection Test Complete!")
    print("=" * 60)
    print()
    print("API Endpoints available:")
    print("  GET  /api/v1/anomaly/check/{public_id}    - Check attachment")
    print("  POST /api/v1/anomaly/pre-upload-check     - Pre-upload check")
    print("  GET  /api/v1/anomaly/scan-all             - Scan all attachments")

    return 0


if __name__ == "__main__":
    sys.exit(main())
