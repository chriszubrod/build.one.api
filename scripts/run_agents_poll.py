#!/usr/bin/env python
"""
Run the agents workflow polling cycle.

This script can be run manually or via a scheduled job (cron, Azure Function).

Usage:
    python scripts/run_agents_poll.py --tenant-id 1
    python scripts/run_agents_poll.py --tenant-id 1 --since 2026-01-01T00:00:00Z
"""
# Python Standard Library Imports
import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def run_poll_cycle(tenant_id: int, since: str = None, access_token: str = None):
    """Run the full polling cycle."""
    from agents.scheduler import WorkflowScheduler
    
    # Get access token if not provided
    if not access_token:
        # TODO: Get from token storage based on tenant
        logger.error("No access token provided. Implement token retrieval from storage.")
        return None
    
    scheduler = WorkflowScheduler()
    
    results = await scheduler.run_full_cycle(
        tenant_id=tenant_id,
        access_token=access_token,
        since=since,
    )
    
    return results


def main():
    parser = argparse.ArgumentParser(description='Run agents workflow polling cycle')
    parser.add_argument('--tenant-id', type=int, required=True, help='Tenant ID')
    parser.add_argument('--since', type=str, help='ISO datetime to filter messages after')
    parser.add_argument('--access-token', type=str, help='MS Graph access token (or set MS_GRAPH_TOKEN env var)')
    parser.add_argument('--yesterday', action='store_true', help='Process emails from yesterday')
    
    args = parser.parse_args()
    
    # Determine the since date
    since = args.since
    if args.yesterday:
        yesterday = datetime.utcnow() - timedelta(days=1)
        since = yesterday.replace(hour=0, minute=0, second=0, microsecond=0).isoformat() + "Z"
    
    # Get access token
    access_token = args.access_token or os.environ.get('MS_GRAPH_TOKEN')
    
    print("\n" + "="*60)
    print("AGENTS WORKFLOW POLLING")
    print("="*60)
    print(f"Tenant ID: {args.tenant_id}")
    print(f"Since: {since or 'Not specified'}")
    print(f"Token: {'Provided' if access_token else 'NOT PROVIDED'}")
    print("="*60 + "\n")
    
    if not access_token:
        print("[ERROR] No access token. Set MS_GRAPH_TOKEN env var or use --access-token")
        sys.exit(1)
    
    # Run the async polling
    results = asyncio.run(run_poll_cycle(
        tenant_id=args.tenant_id,
        since=since,
        access_token=access_token,
    ))
    
    if results:
        print("\n" + "="*60)
        print("RESULTS")
        print("="*60)
        print(f"  New workflows created: {results.get('new_workflows', 0)}")
        print(f"  Replies processed: {results.get('replies_processed', 0)}")
        print(f"  Orphan emails matched: {results.get('orphans_matched', 0)}")
        print(f"  Reminders sent: {results.get('reminders_sent', 0)}")
        print(f"  Workflows abandoned: {results.get('abandoned', 0)}")
        print("="*60 + "\n")
    else:
        print("\n[ERROR] Polling cycle failed\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
