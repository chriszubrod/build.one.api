#!/usr/bin/env python3
"""
Cleanup duplicate attachment blobs from workflows.

This script:
1. Finds workflows with multiple attachment_blob_urls
2. Identifies duplicates (same filename, different blob paths)
3. Keeps only one copy and deletes the rest
4. Updates the workflow context

Usage:
    python scripts/cleanup_duplicate_attachments.py [--dry-run]
"""

import argparse
import asyncio
import json
import logging
import re
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import unquote

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.database import get_connection
from workflows.persistence.repo import WorkflowRepository
from workflows.capabilities.registry import get_capability_registry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def extract_filename_from_url(blob_url: str) -> str:
    """Extract the original filename from a blob URL."""
    # URL format: https://storage.blob.core.windows.net/container/workflows/{id}/{prefix}_{filename}
    # The filename is URL-encoded
    path = blob_url.split("/")[-1]  # Get last segment
    path = unquote(path)  # URL decode
    
    # Remove the random prefix (8 chars + underscore)
    # Format: xxxxxxxx_filename.ext
    match = re.match(r"^[a-f0-9]{8}_(.+)$", path)
    if match:
        return match.group(1)
    return path


def get_workflows_with_attachments() -> list:
    """Find workflows that have attachment_blob_urls."""
    workflows = []
    
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                PublicId,
                Context
            FROM dbo.Workflow
            WHERE JSON_QUERY(Context, '$.attachment_blob_urls') IS NOT NULL
        """)
        
        for row in cursor.fetchall():
            public_id = str(row[0])
            context = json.loads(row[1]) if row[1] else {}
            blob_urls = context.get("attachment_blob_urls", [])
            
            if len(blob_urls) > 1:
                workflows.append({
                    "public_id": public_id,
                    "context": context,
                    "blob_urls": blob_urls,
                })
    
    return workflows


def find_duplicates(blob_urls: list) -> dict:
    """
    Group blob URLs by filename and identify duplicates.
    
    Returns dict mapping filename -> list of URLs (first is kept, rest are duplicates)
    """
    by_filename = defaultdict(list)
    
    for url in blob_urls:
        filename = extract_filename_from_url(url)
        by_filename[filename].append(url)
    
    return dict(by_filename)


def delete_blob(blob_url: str, capabilities, dry_run: bool = False) -> bool:
    """Delete a blob from Azure storage using the storage capability."""
    if dry_run:
        logger.info(f"    [DRY RUN] Would delete: {blob_url}")
        return True
    
    try:
        result = capabilities.storage.delete_blob(blob_url)
        if result.success:
            # Extract just the blob path for cleaner logging
            blob_path = "/".join(blob_url.split("/")[-3:])
            logger.info(f"    ✓ Deleted: {blob_path}")
            return True
        else:
            logger.error(f"    ✗ Failed to delete {blob_url}: {result.error}")
            return False
        
    except Exception as e:
        logger.error(f"    ✗ Failed to delete {blob_url}: {e}")
        return False


async def cleanup_workflow(workflow: dict, capabilities, dry_run: bool = False) -> dict:
    """
    Clean up duplicate attachments for a single workflow.
    
    Returns dict with results.
    """
    public_id = workflow["public_id"]
    blob_urls = workflow["blob_urls"]
    context = workflow["context"]
    
    logger.info(f"Processing workflow {public_id} ({len(blob_urls)} blob URLs)...")
    
    results = {
        "public_id": public_id,
        "original_count": len(blob_urls),
        "final_count": 0,
        "deleted_count": 0,
        "errors": [],
    }
    
    # Group by filename
    by_filename = find_duplicates(blob_urls)
    
    # For each filename, keep the first URL and delete the rest
    urls_to_keep = []
    urls_to_delete = []
    
    for filename, urls in by_filename.items():
        urls_to_keep.append(urls[0])  # Keep first
        if len(urls) > 1:
            urls_to_delete.extend(urls[1:])  # Delete rest
            logger.info(f"  {filename}: keeping 1, deleting {len(urls) - 1} duplicates")
    
    results["final_count"] = len(urls_to_keep)
    
    # Delete duplicates
    for url in urls_to_delete:
        success = delete_blob(url, capabilities, dry_run)
        if success:
            results["deleted_count"] += 1
        else:
            results["errors"].append(f"Failed to delete {url}")
    
    # Update workflow context
    if urls_to_delete and not dry_run:
        context["attachment_blob_urls"] = urls_to_keep
        
        repo = WorkflowRepository()
        repo.update_state(
            public_id=public_id,
            state=workflow["context"].get("state", "completed"),
            context=context,
        )
        logger.info(f"  ✓ Updated workflow context ({len(urls_to_keep)} URLs)")
    
    return results


async def main():
    parser = argparse.ArgumentParser(description="Cleanup duplicate attachment blobs")
    parser.add_argument("--dry-run", action="store_true", help="Don't actually delete, just show what would happen")
    args = parser.parse_args()
    
    logger.info("=" * 60)
    logger.info("Cleanup Duplicate Attachments")
    logger.info("=" * 60)
    
    if args.dry_run:
        logger.info("DRY RUN MODE - no changes will be made")
    
    # Find workflows with multiple attachments
    workflows = get_workflows_with_attachments()
    logger.info(f"Found {len(workflows)} workflows with multiple attachment URLs")
    
    if not workflows:
        logger.info("Nothing to clean up!")
        return 0
    
    # Get capabilities for blob operations
    capabilities = get_capability_registry()
    
    # Process each workflow
    total_deleted = 0
    total_errors = 0
    
    for workflow in workflows:
        result = await cleanup_workflow(workflow, capabilities, dry_run=args.dry_run)
        total_deleted += result["deleted_count"]
        total_errors += len(result["errors"])
    
    # Summary
    logger.info("")
    logger.info("=" * 60)
    logger.info("Summary")
    logger.info("=" * 60)
    logger.info(f"Workflows processed: {len(workflows)}")
    logger.info(f"Blobs deleted: {total_deleted}")
    logger.info(f"Errors: {total_errors}")
    
    return 0 if total_errors == 0 else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
