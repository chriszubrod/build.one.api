#!/usr/bin/env python3
"""
Backfill attachment blob URLs for existing email_intake workflows.

This script:
1. Finds all email_intake workflows confirmed as "bill" that are missing attachment_blob_urls
2. Re-fetches attachments from MS Graph using the original message_id
3. Downloads and stores them to blob storage
4. Updates the workflow context with the blob URLs

Usage:
    python scripts/backfill_workflow_attachments.py [--dry-run]
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.database import get_connection
from workflows.persistence.repo import WorkflowRepository
from workflows.capabilities.registry import get_capability_registry
from integrations.ms.auth.persistence.repo import MsAuthRepository

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def get_access_token() -> str:
    """Get MS Graph access token from database."""
    ms_auth_repo = MsAuthRepository()
    auths = ms_auth_repo.read_all()
    if auths and len(auths) > 0:
        return auths[0].access_token
    raise RuntimeError("No MS Graph access token available")


def get_workflows_needing_backfill() -> list:
    """Find email_intake workflows that need attachment backfill."""
    workflows = []
    
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                PublicId,
                TriggerMessageId,
                Context
            FROM dbo.Workflow
            WHERE WorkflowType = 'email_intake'
              AND State = 'completed'
              AND JSON_VALUE(Context, '$.confirmed_entity_type') = 'bill'
              AND (
                  JSON_VALUE(Context, '$.attachment_blob_urls') IS NULL
                  OR JSON_VALUE(Context, '$.attachment_blob_urls') = '[]'
              )
        """)
        
        for row in cursor.fetchall():
            public_id = str(row[0])
            trigger_message_id = row[1]
            context = json.loads(row[2]) if row[2] else {}
            
            # Check if workflow has attachments metadata but no blob URLs
            attachments = context.get("attachments", [])
            all_attachments = context.get("all_conversation_attachments", [])
            
            if attachments or all_attachments:
                workflows.append({
                    "public_id": public_id,
                    "trigger_message_id": trigger_message_id,
                    "context": context,
                    "attachments_count": len(attachments) + len(all_attachments),
                })
    
    return workflows


async def backfill_workflow_attachments(
    workflow: dict,
    access_token: str,
    capabilities,
    dry_run: bool = False,
) -> dict:
    """
    Backfill attachment blob URLs for a single workflow.
    
    Returns dict with results.
    """
    public_id = workflow["public_id"]
    trigger_message_id = workflow["trigger_message_id"]
    context = workflow["context"]
    
    logger.info(f"Processing workflow {public_id}...")
    
    results = {
        "public_id": public_id,
        "success": False,
        "attachments_processed": 0,
        "blob_urls": [],
        "errors": [],
    }
    
    if not trigger_message_id:
        results["errors"].append("No trigger_message_id")
        return results
    
    try:
        # Get all message IDs that might have attachments
        message_ids_to_check = set()
        message_ids_to_check.add(trigger_message_id)
        
        # Also check all_conversation_attachments for message IDs
        for att in context.get("all_conversation_attachments", []):
            if att.get("message_id"):
                message_ids_to_check.add(att["message_id"])
        
        blob_urls = []
        seen_filenames = set()  # Track filenames to avoid duplicates
        
        for message_id in message_ids_to_check:
            # Fetch attachments list from MS Graph
            from integrations.ms.mail.external.client import list_message_attachments, download_attachment
            
            attachments_result = list_message_attachments(message_id)
            
            if attachments_result.get("status_code") != 200:
                logger.warning(f"  Failed to list attachments for message {message_id}: {attachments_result.get('message')}")
                continue
            
            attachments = attachments_result.get("attachments", [])
            logger.info(f"  Found {len(attachments)} attachments in message {message_id}")
            
            for att in attachments:
                attachment_id = att.get("attachment_id")
                name = att.get("name", "attachment")
                content_type = att.get("content_type", "application/octet-stream")
                
                if not attachment_id:
                    continue
                
                # Skip inline images (typically signatures)
                if att.get("is_inline"):
                    logger.info(f"    Skipping inline attachment: {name}")
                    continue
                
                # Skip non-document types
                if not _is_processable_attachment(content_type, name):
                    logger.info(f"    Skipping non-processable attachment: {name} ({content_type})")
                    continue
                
                # Skip duplicates (same file forwarded in replies)
                if name in seen_filenames:
                    logger.info(f"    Skipping duplicate: {name}")
                    continue
                seen_filenames.add(name)
                
                if dry_run:
                    logger.info(f"    [DRY RUN] Would download and store: {name}")
                    results["attachments_processed"] += 1
                    continue
                
                # Download attachment
                download_result = download_attachment(message_id, attachment_id)
                
                if download_result.get("status_code") != 200:
                    results["errors"].append(f"Failed to download {name}: {download_result.get('message')}")
                    continue
                
                content = download_result.get("content")
                if not content:
                    results["errors"].append(f"No content for {name}")
                    continue
                
                # Save to blob storage
                save_result = capabilities.storage.save_workflow_attachment(
                    workflow_public_id=public_id,
                    file_content=content,
                    filename=name,
                    content_type=content_type,
                )
                
                if save_result.success:
                    blob_url = save_result.data.get("blob_url")
                    blob_urls.append(blob_url)
                    results["attachments_processed"] += 1
                    logger.info(f"    ✓ Saved {name} to blob storage")
                else:
                    results["errors"].append(f"Failed to save {name}: {save_result.error}")
        
        results["blob_urls"] = blob_urls
        
        # Update workflow context with blob URLs
        if blob_urls and not dry_run:
            repo = WorkflowRepository()
            
            # Merge with existing context
            context["attachment_blob_urls"] = blob_urls
            
            # Also update attachment metadata with IDs
            updated_attachments = []
            for att in context.get("attachments", []):
                # Try to find matching attachment from our fetch
                updated_attachments.append(att)
            context["attachments"] = updated_attachments
            
            repo.update_state(
                public_id=public_id,
                state="completed",  # Keep same state
                context=context,
            )
            logger.info(f"  ✓ Updated workflow context with {len(blob_urls)} blob URLs")
        
        results["success"] = True
        
    except Exception as e:
        logger.exception(f"Error processing workflow {public_id}")
        results["errors"].append(str(e))
    
    return results


def _is_processable_attachment(content_type: str, filename: str) -> bool:
    """Check if an attachment can be processed."""
    processable_types = [
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/jpg",
        "image/tiff",
        "image/bmp",
    ]
    
    # Check content type
    if any(pt in content_type.lower() for pt in processable_types):
        return True
    
    # Check file extension as fallback
    ext = filename.lower().split(".")[-1] if "." in filename else ""
    processable_extensions = ["pdf", "png", "jpg", "jpeg", "tiff", "bmp"]
    
    return ext in processable_extensions


async def main():
    parser = argparse.ArgumentParser(description="Backfill attachment blob URLs for workflows")
    parser.add_argument("--dry-run", action="store_true", help="Don't actually download/store, just show what would happen")
    args = parser.parse_args()
    
    logger.info("=" * 60)
    logger.info("Backfill Workflow Attachments")
    logger.info("=" * 60)
    
    if args.dry_run:
        logger.info("DRY RUN MODE - no changes will be made")
    
    # Get access token
    try:
        access_token = get_access_token()
        logger.info("✓ Got MS Graph access token")
    except Exception as e:
        logger.error(f"✗ Failed to get access token: {e}")
        return 1
    
    # Get capabilities
    capabilities = get_capability_registry()
    
    # Find workflows needing backfill
    workflows = get_workflows_needing_backfill()
    logger.info(f"Found {len(workflows)} workflows needing attachment backfill")
    
    if not workflows:
        logger.info("Nothing to do!")
        return 0
    
    # Process each workflow
    success_count = 0
    error_count = 0
    total_attachments = 0
    
    for workflow in workflows:
        result = await backfill_workflow_attachments(
            workflow,
            access_token,
            capabilities,
            dry_run=args.dry_run,
        )
        
        if result["success"]:
            success_count += 1
            total_attachments += result["attachments_processed"]
        else:
            error_count += 1
            for err in result["errors"]:
                logger.error(f"  Error: {err}")
    
    # Summary
    logger.info("")
    logger.info("=" * 60)
    logger.info("Summary")
    logger.info("=" * 60)
    logger.info(f"Workflows processed: {len(workflows)}")
    logger.info(f"  Successful: {success_count}")
    logger.info(f"  Errors: {error_count}")
    logger.info(f"Total attachments saved: {total_attachments}")
    
    return 0 if error_count == 0 else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
