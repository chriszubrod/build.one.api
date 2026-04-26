"""V1 smoke test for the email-agent pipeline.

Drives the full Phase 1 path against prod (or any API base URL):

  1. POST /admin/email/poll
  2. GET  /get/email-messages?processing_status=pending
  3. For each pending message + each attachment:
        POST /admin/email/extract/{attachment_public_id}
  4. GET /get/email-message/{public_id} → final state

Reads from local config:
  - api_base = https://{azure_default_domain}
  - drain_secret (X-Drain-Secret header)

Read endpoints require a JWT, so we POST /mobile/auth/login with creds
the user supplies via the SMOKE_USERNAME / SMOKE_PASSWORD env vars
(or --username / --password flags).

Usage:
    SMOKE_USERNAME=chris@example.com SMOKE_PASSWORD=... \\
      .venv/bin/python scripts/smoke_email_v1.py

    # local API instead of prod:
    SMOKE_API_BASE=http://localhost:8000 ...
"""
import argparse
import getpass
import json
import os
import sys
import time
from typing import Optional

import httpx

# Allow running from the repo root: import config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402


def _resolve_api_base() -> str:
    explicit = os.environ.get("SMOKE_API_BASE")
    if explicit:
        return explicit.rstrip("/")
    domain = (config.Settings().azure_default_domain or "").strip()
    if not domain:
        raise SystemExit(
            "No API base URL — set SMOKE_API_BASE or azure_default_domain in .env"
        )
    return f"https://{domain}"


def _resolve_drain_secret() -> str:
    secret = (config.Settings().drain_secret or "").strip()
    if not secret:
        raise SystemExit("No drain_secret in .env — cannot call admin endpoints")
    return secret


def _login(api_base: str, username: str, password: str) -> str:
    print(f"→ POST {api_base}/api/v1/mobile/auth/login (user={username})")
    with httpx.Client(timeout=30.0) as client:
        r = client.post(
            f"{api_base}/api/v1/mobile/auth/login",
            json={"username": username, "password": password},
        )
        if r.status_code != 200:
            raise SystemExit(f"Login failed: {r.status_code} {r.text[:300]}")
        body = r.json()
        token = (body.get("data", {}).get("token") or {}).get("access_token")
        if not token:
            raise SystemExit(f"Login response missing access_token: {body}")
        print(f"  ✓ logged in")
        return token


def _poll(api_base: str, drain_secret: str) -> dict:
    print(f"→ POST {api_base}/api/v1/admin/email/poll")
    with httpx.Client(timeout=120.0) as client:
        r = client.post(
            f"{api_base}/api/v1/admin/email/poll",
            headers={"X-Drain-Secret": drain_secret},
        )
        if r.status_code != 200:
            raise SystemExit(f"Poll failed: {r.status_code} {r.text[:500]}")
        body = r.json()
        result = body.get("result") or {}
        print(f"  duration:  {body.get('duration_ms')}ms")
        print(f"  polled:    {result.get('polled')}")
        print(f"  new:       {result.get('new_messages')}")
        print(f"  uploads:   {result.get('attachments_uploaded')}")
        if result.get("errors"):
            print(f"  errors:    {result.get('errors')}")
        return body


def _list_pending(api_base: str, token: str) -> list[dict]:
    print(f"→ GET  {api_base}/api/v1/get/email-messages?processing_status=pending&page_size=20")
    with httpx.Client(timeout=30.0) as client:
        r = client.get(
            f"{api_base}/api/v1/get/email-messages",
            params={"processing_status": "pending", "page_size": 20},
            headers={"Authorization": f"Bearer {token}"},
        )
        if r.status_code != 200:
            raise SystemExit(f"List pending failed: {r.status_code} {r.text[:500]}")
        body = r.json()
        rows = body.get("data") or []
        print(f"  pending count: {len(rows)}")
        for row in rows:
            print(f"    - public_id={row['public_id']}")
            print(f"      from={row.get('from_address')}  subject={row.get('subject')!r}")
            print(f"      received={row.get('received_datetime')}  has_attachments={row.get('has_attachments')}")
        return rows


def _read_email(api_base: str, token: str, public_id: str) -> dict:
    with httpx.Client(timeout=30.0) as client:
        r = client.get(
            f"{api_base}/api/v1/get/email-message/{public_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        if r.status_code != 200:
            raise SystemExit(f"Read email failed: {r.status_code} {r.text[:500]}")
        return (r.json() or {}).get("data") or {}


def _extract(api_base: str, drain_secret: str, attachment_public_id: str) -> dict:
    print(f"→ POST {api_base}/api/v1/admin/email/extract/{attachment_public_id}")
    with httpx.Client(timeout=180.0) as client:
        r = client.post(
            f"{api_base}/api/v1/admin/email/extract/{attachment_public_id}",
            headers={"X-Drain-Secret": drain_secret},
        )
        if r.status_code != 200:
            raise SystemExit(f"Extract failed: {r.status_code} {r.text[:500]}")
        body = r.json()
        result = body.get("result") or {}
        print(f"  duration:    {body.get('duration_ms')}ms")
        print(f"  status:      {result.get('status')}")
        print(f"  vendor:      {result.get('vendor_name')!r}")
        print(f"  invoice #:   {result.get('invoice_number')!r}")
        print(f"  total:       {result.get('total_amount')} {result.get('currency') or ''}")
        print(f"  confidence:  {result.get('confidence')}")
        if result.get("validation"):
            v = result["validation"]
            print(f"  valid:       {v.get('is_valid')}  issues={v.get('issues')}")
        return body


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", default=os.environ.get("SMOKE_USERNAME"))
    parser.add_argument("--password", default=os.environ.get("SMOKE_PASSWORD"))
    parser.add_argument("--no-extract", action="store_true",
                        help="Skip the DI extraction step (just poll + list)")
    args = parser.parse_args()

    api_base = _resolve_api_base()
    drain_secret = _resolve_drain_secret()
    print(f"API base: {api_base}")
    print(f"DRAIN_SECRET: configured (len={len(drain_secret)})")
    print()

    username = args.username
    if not username:
        username = input("Username (email): ").strip()
    password = args.password
    if not password:
        password = getpass.getpass("Password: ")

    token = _login(api_base, username, password)
    print()

    poll_body = _poll(api_base, drain_secret)
    print()

    pending = _list_pending(api_base, token)
    print()

    if not pending:
        print("No pending messages — either nothing was tagged 'Agent: Process' or already processed.")
        print("Done.")
        return

    if args.no_extract:
        print("(skipping DI extraction per --no-extract)")
        return

    for email in pending:
        print(f"--- email {email['public_id']} ({email.get('subject')!r}) ---")
        full = _read_email(api_base, token, email["public_id"])
        attachments = full.get("attachments") or []
        if not attachments:
            print("  (no attachments)")
            continue
        for att in attachments:
            if att.get("is_inline"):
                print(f"  - {att['filename']} (inline — skipping)")
                continue
            if att.get("extraction_status") == "extracted":
                print(f"  - {att['filename']} already extracted; skipping")
                continue
            _extract(api_base, drain_secret, att["public_id"])
        print()
        # Re-read to show final state
        final = _read_email(api_base, token, email["public_id"])
        print(f"  final processing_status: {final.get('processing_status')}")
        for att in (final.get("attachments") or []):
            if att.get("is_inline"):
                continue
            print(f"    - {att['filename']}: extraction_status={att.get('extraction_status')} "
                  f"vendor={att.get('di_vendor_name')!r} total={att.get('di_total_amount')}")
        print()


if __name__ == "__main__":
    main()
