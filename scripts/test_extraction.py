#!/usr/bin/env python3
"""
Test script for Phase 2: Document Extraction

Tests the extraction service with a sample PDF to verify:
1. Azure Document Intelligence connection
2. Extraction result saved to blob storage
3. Database updated with blob URL
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.attachment.business.extraction_service import ExtractionService
from services.attachment.business.service import AttachmentService
from shared.storage import AzureBlobStorage
import uuid


def create_sample_pdf() -> bytes:
    """Create a minimal valid PDF for testing."""
    # Minimal PDF with text content
    pdf_content = b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]
   /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>
endobj
4 0 obj
<< /Length 44 >>
stream
BT
/F1 24 Tf
100 700 Td
(Hello World) Tj
ET
endstream
endobj
5 0 obj
<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>
endobj
xref
0 6
0000000000 65535 f 
0000000009 00000 n 
0000000058 00000 n 
0000000115 00000 n 
0000000266 00000 n 
0000000359 00000 n 
trailer
<< /Size 6 /Root 1 0 R >>
startxref
434
%%EOF"""
    return pdf_content


def main():
    print("=" * 60)
    print("Phase 2: Document Extraction Test")
    print("=" * 60)
    print()
    
    # Step 1: Upload a test PDF to blob storage
    print("1. Uploading test PDF to blob storage...")
    storage = AzureBlobStorage()
    attachment_service = AttachmentService()
    extraction_service = ExtractionService()
    
    pdf_content = create_sample_pdf()
    public_id = str(uuid.uuid4())
    blob_name = f"{public_id}.pdf"
    
    try:
        blob_url = storage.upload_file(
            blob_name=blob_name,
            file_content=pdf_content,
            content_type="application/pdf"
        )
        print(f"   Uploaded: {blob_url}")
    except Exception as e:
        print(f"   FAILED: {e}")
        return 1
    
    # Step 2: Create attachment record
    print("\n2. Creating attachment record in database...")
    try:
        attachment = attachment_service.create(
            filename=blob_name,
            original_filename="test_extraction.pdf",
            file_extension="pdf",
            content_type="application/pdf",
            file_size=len(pdf_content),
            file_hash=attachment_service.calculate_hash(pdf_content),
            blob_url=blob_url,
            description="Test attachment for extraction",
            category="test",
        )
        print(f"   Created: ID={attachment.id}, PublicID={attachment.public_id}")
        print(f"   Extraction Status: {attachment.extraction_status}")
    except Exception as e:
        print(f"   FAILED: {e}")
        return 1
    
    # Step 3: Run extraction
    print("\n3. Running extraction via Azure Document Intelligence...")
    try:
        updated = extraction_service.extract_attachment(attachment)
        print(f"   Extraction Status: {updated.extraction_status}")
        if updated.extraction_status == "completed":
            print(f"   Extraction Blob URL: {updated.extracted_text_blob_url}")
        elif updated.extraction_status == "failed":
            print(f"   Error: {updated.extraction_error}")
    except Exception as e:
        print(f"   FAILED: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    # Step 4: Retrieve and display extracted text
    if updated.extraction_status == "completed":
        print("\n4. Retrieving extracted text from blob storage...")
        try:
            result = extraction_service.get_extraction_result(updated)
            if result:
                print(f"   Content length: {len(result.get('content', ''))} chars")
                print(f"   Pages: {len(result.get('pages', []))}")
                print(f"   Tables: {len(result.get('tables', []))}")
                print(f"   Paragraphs: {len(result.get('paragraphs', []))}")
                print(f"   Key-Value Pairs: {len(result.get('key_value_pairs', []))}")
                print()
                print("   Extracted Content Preview:")
                print("   " + "-" * 40)
                content = result.get('content', '')[:500]
                for line in content.split('\n')[:10]:
                    print(f"   {line}")
                if len(result.get('content', '')) > 500:
                    print("   ...")
            else:
                print("   Failed to retrieve extraction result")
        except Exception as e:
            print(f"   FAILED: {e}")
            return 1
    
    # Step 5: Cleanup (optional)
    print("\n5. Cleanup...")
    print("   Skipping cleanup - keeping test data for inspection")
    print(f"   To manually delete: Attachment ID={attachment.id}, PublicID={attachment.public_id}")
    
    print()
    print("=" * 60)
    if updated.extraction_status == "completed":
        print("TEST PASSED: Extraction completed successfully!")
    else:
        print(f"TEST FAILED: Extraction status is '{updated.extraction_status}'")
        return 1
    print("=" * 60)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
