#!/usr/bin/env python3
"""
Test script for Phase 6: Auto-Categorization

Tests:
1. Document categorization with GPT-4o-mini
2. Field extraction
3. Confidence thresholds
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.categorization.business.service import get_categorization_service
from services.categorization.business.model import DocumentCategory, CategorizationStatus
from services.attachment.business.service import AttachmentService


def main():
    print("=" * 60)
    print("Phase 6: Auto-Categorization Test")
    print("=" * 60)
    print()

    categorization_service = get_categorization_service()
    attachment_service = AttachmentService()

    # Test 1: List available categories
    print("1. Available document categories:")
    for cat in DocumentCategory:
        print(f"   - {cat.value}: {cat.name.replace('_', ' ').title()}")
    print()

    # Test 2: Categorize sample text (invoice-like)
    print("2. Testing text categorization with sample invoice...")
    sample_invoice = """
    INVOICE
    
    From: ABC Construction Supplies
    123 Main Street
    Anytown, USA 12345
    
    Invoice Number: INV-2024-0001
    Date: January 15, 2024
    Due Date: February 15, 2024
    
    Bill To:
    XYZ Contractors
    456 Oak Avenue
    Somewhere, USA 67890
    
    Description                          Qty     Unit Price      Amount
    -------------------------------------------------------------------
    Lumber - 2x4x8                       100     $5.00           $500.00
    Concrete Mix (80lb bags)             50      $8.00           $400.00
    Rebar #4 (20ft)                      25      $12.00          $300.00
    
    Subtotal:                                                    $1,200.00
    Tax (8%):                                                    $96.00
    Total Due:                                                   $1,296.00
    
    Payment Terms: Net 30
    """

    try:
        result = categorization_service.categorize_text(sample_invoice, "invoice_sample.pdf")
        
        if result:
            print(f"   Category: {result.category.value}")
            print(f"   Confidence: {result.confidence:.0%}")
            print(f"   Status: {result.status.value}")
            print(f"   Reasoning: {result.reasoning}")
            
            if result.extracted_fields:
                print("   Extracted Fields:")
                fields = result.extracted_fields.to_dict()
                for key, value in fields.items():
                    if key != 'raw_fields' and value:
                        print(f"      - {key}: {value}")
            
            # Check thresholds
            print()
            print(f"   High confidence (>95%): {result.is_high_confidence}")
            print(f"   Medium confidence (70-95%): {result.is_medium_confidence}")
            print(f"   Low confidence (<70%): {result.is_low_confidence}")
        else:
            print("   ERROR: Categorization failed")
    except Exception as e:
        print(f"   ERROR: {e}")
        import traceback
        traceback.print_exc()
    print()

    # Test 3: Categorize sample text (contract-like)
    print("3. Testing text categorization with sample contract...")
    sample_contract = """
    CONSTRUCTION CONTRACT AGREEMENT
    
    This Agreement is entered into as of March 1, 2024
    
    BETWEEN:
    
    Owner: Johnson Properties LLC
    Address: 789 Business Park Drive
    
    AND
    
    Contractor: Premier Builders Inc.
    License No: CBC123456
    
    PROJECT: New Office Building Construction
    Location: 1000 Commerce Street
    
    SCOPE OF WORK:
    The Contractor agrees to perform all work necessary for the construction
    of a 3-story commercial office building as per the attached specifications.
    
    CONTRACT AMOUNT: $2,500,000.00
    
    SCHEDULE:
    Start Date: April 1, 2024
    Completion Date: December 31, 2024
    
    TERMS AND CONDITIONS:
    [Standard construction contract terms...]
    
    SIGNATURES:
    _________________________    _________________________
    Owner                        Contractor
    """

    try:
        result = categorization_service.categorize_text(sample_contract, "construction_contract.pdf")
        
        if result:
            print(f"   Category: {result.category.value}")
            print(f"   Confidence: {result.confidence:.0%}")
            print(f"   Status: {result.status.value}")
            print(f"   Reasoning: {result.reasoning}")
        else:
            print("   ERROR: Categorization failed")
    except Exception as e:
        print(f"   ERROR: {e}")
    print()

    # Test 4: Categorize an actual attachment
    print("4. Testing categorization on actual attachment...")
    attachments = attachment_service.read_all()
    extracted = [a for a in attachments if a.extraction_status == "completed"]
    
    if extracted:
        test_attachment = extracted[0]
        print(f"   Using: {test_attachment.original_filename} (ID: {test_attachment.id})")
        
        try:
            result = categorization_service.categorize_attachment(test_attachment)
            
            if result:
                print(f"   Category: {result.category.value}")
                print(f"   Confidence: {result.confidence:.0%}")
                print(f"   Status: {result.status.value}")
                print(f"   Reasoning: {result.reasoning}")
                
                # Show suggested actions
                actions = categorization_service.get_category_actions(result.category)
                if actions:
                    print(f"   Suggested Action: {actions.get('suggested_action', 'None')}")
            else:
                print("   Could not categorize (no extracted text?)")
        except Exception as e:
            print(f"   ERROR: {e}")
            import traceback
            traceback.print_exc()
    else:
        print("   No extracted attachments found. Run test_extraction.py first.")
    print()

    # Test 5: Show confidence thresholds
    print("5. Confidence threshold configuration:")
    print(f"   High (auto-assign): >= 95%")
    print(f"   Medium (suggest):   70% - 94%")
    print(f"   Low (manual):       < 70%")
    print()

    print("=" * 60)
    print("Phase 6 Auto-Categorization Test Complete!")
    print("=" * 60)
    print()
    print("API Endpoints available:")
    print("  GET  /api/v1/categorization/categories         - List categories")
    print("  POST /api/v1/categorization/categorize/{id}    - Categorize attachment")
    print("  POST /api/v1/categorization/categorize-text    - Categorize raw text")
    print("  POST /api/v1/categorization/confirm/{id}       - Confirm/reject category")
    print("  GET  /api/v1/categorization/pending            - Get pending items")
    print("  POST /api/v1/categorization/batch              - Batch categorize")

    return 0


if __name__ == "__main__":
    sys.exit(main())
