#!/usr/bin/env python3
"""
Backfill Task entries for existing workflows.

This script:
1. Finds all workflows that don't have a corresponding Task entry
2. Creates Task entries for them using the create_from_workflow() bridge method
3. Links the Task to the Workflow via WorkflowId

Usage:
    python scripts/backfill_tasks_from_workflows.py [--dry-run] [--limit N]
"""

import argparse
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.database import get_connection
from workflows.persistence.repo import WorkflowRepository
from services.tasks.business.service import TaskService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def get_workflows_needing_tasks(limit: int = None) -> list:
    """
    Find all workflows that don't have a corresponding Task entry.

    Returns list of workflow public_ids.
    """
    workflows = []

    with get_connection() as conn:
        cursor = conn.cursor()

        # Find workflows without a linked Task
        query = """
            SELECT
                w.PublicId,
                w.WorkflowType,
                w.ConversationId,
                w.State,
                w.CreatedDatetime
            FROM dbo.Workflow w
            LEFT JOIN dbo.Task t ON t.WorkflowId = w.Id
            WHERE t.Id IS NULL
            ORDER BY w.CreatedDatetime DESC
        """

        if limit:
            query += f"\nOFFSET 0 ROWS FETCH NEXT {limit} ROWS ONLY"

        cursor.execute(query)

        for row in cursor.fetchall():
            public_id = str(row[0])
            workflow_type = row[1]
            conversation_id = row[2]
            state = row[3]
            created_datetime = str(row[4]) if row[4] else None

            workflows.append({
                "public_id": public_id,
                "workflow_type": workflow_type,
                "conversation_id": conversation_id,
                "state": state,
                "created_datetime": created_datetime,
            })

    return workflows


def backfill_task_for_workflow(
    workflow_info: dict,
    dry_run: bool = False,
) -> dict:
    """
    Create a Task entry for a single workflow.

    Returns dict with results.
    """
    public_id = workflow_info["public_id"]

    logger.info(f"Processing workflow {public_id}...")

    results = {
        "public_id": public_id,
        "success": False,
        "task_public_id": None,
        "error": None,
    }

    try:
        if dry_run:
            logger.info(f"  [DRY RUN] Would create Task for workflow {public_id}")
            results["success"] = True
            return results

        # Get the full workflow object
        repo = WorkflowRepository()
        workflow = repo.read_by_public_id(public_id)

        if not workflow:
            results["error"] = "Workflow not found"
            logger.warning(f"  ✗ Workflow not found: {public_id}")
            return results

        # Determine source_type based on workflow_type
        source_type = "email"  # Most workflows are from email
        if workflow.workflow_type == "data_upload":
            source_type = "upload"
        elif workflow.workflow_type == "manual":
            source_type = "manual"

        # Create Task using the bridge method
        task_service = TaskService()
        task = task_service.create_from_workflow(
            workflow=workflow,
            source_type=source_type,
            source_id=workflow.conversation_id,
        )

        if task:
            results["success"] = True
            results["task_public_id"] = task.public_id
            logger.info(f"  ✓ Created Task {task.public_id} for workflow {public_id}")
        else:
            results["error"] = "create_from_workflow returned None"
            logger.warning(f"  ✗ Failed to create Task for workflow {public_id}")

    except Exception as e:
        logger.exception(f"Error processing workflow {public_id}")
        results["error"] = str(e)

    return results


def main():
    parser = argparse.ArgumentParser(description="Backfill Task entries from existing workflows")
    parser.add_argument("--dry-run", action="store_true", help="Don't actually create tasks, just show what would happen")
    parser.add_argument("--limit", type=int, help="Limit number of workflows to process")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("Backfill Tasks from Workflows")
    logger.info("=" * 60)

    if args.dry_run:
        logger.info("DRY RUN MODE - no changes will be made")

    if args.limit:
        logger.info(f"LIMIT: Processing at most {args.limit} workflows")

    # Find workflows needing Task entries
    workflows = get_workflows_needing_tasks(limit=args.limit)
    logger.info(f"Found {len(workflows)} workflows needing Task entries")

    if not workflows:
        logger.info("Nothing to do!")
        return 0

    # Show breakdown by workflow type
    type_counts = {}
    for wf in workflows:
        wf_type = wf.get("workflow_type", "unknown")
        type_counts[wf_type] = type_counts.get(wf_type, 0) + 1

    logger.info("Breakdown by workflow type:")
    for wf_type, count in sorted(type_counts.items()):
        logger.info(f"  {wf_type}: {count}")

    # Process each workflow
    success_count = 0
    error_count = 0

    for workflow_info in workflows:
        result = backfill_task_for_workflow(
            workflow_info,
            dry_run=args.dry_run,
        )

        if result["success"]:
            success_count += 1
        else:
            error_count += 1
            if result["error"]:
                logger.error(f"  Error: {result['error']}")

    # Summary
    logger.info("")
    logger.info("=" * 60)
    logger.info("Summary")
    logger.info("=" * 60)
    logger.info(f"Workflows processed: {len(workflows)}")
    logger.info(f"  Successful: {success_count}")
    logger.info(f"  Errors: {error_count}")

    return 0 if error_count == 0 else 1


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
