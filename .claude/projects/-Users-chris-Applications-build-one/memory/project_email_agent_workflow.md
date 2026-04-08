---
name: Email Agent Workflow — Phase 1 Intake & Routing
description: Complete workflow design for EmailAgent Phase 1 — from email discovery through PM review, including classification, extraction, deduplication, entity resolution, draft creation, and routing.
type: project
---

## Email Agent — Phase 1: Intake & Routing

The EmailAgent monitors a configured MS Graph mailbox. Users manually tag emails with "Blue category" in Outlook to add them to the agent's work queue.

### Entry Point: New or Existing Thread?

Every email starts with one question: **is this part of an existing conversation?**

- **Existing thread** — This is a reply in a conversation the agent is already tracking.
  - **Approved** → update bill status, advance workflow
  - **Not approved** → flag for user, requires human interaction
- **New item** → process from scratch (Steps 1–6 below)

Thread detection uses MS Graph `conversation_id` + InboxRecord lookup.

### New Item Processing Pipeline

**Step 1: Identify**
Classify the email type: bill, credit memo, expense, statement, inquiry.
- Fast heuristic (subject/sender/attachment pattern matching) for list views
- Claude Haiku single-call for full classification with higher confidence
- Heuristic is fallback if Haiku fails

**Step 2: Extract**
Pull structured fields from the attachment via OCR + Claude:
- Vendor name, bill number, bill date, due date, total amount, memo
- Line items (description, quantity, rate, amount)
- Project hints, cost code hints, payment terms
- Pipeline: Azure Document Intelligence OCR → Claude Haiku field mapping → heuristic fallback

**Step 3: Deduplicate**
Three checks before proceeding:
1. **Same email?** — message ID already in InboxRecord → skip
2. **Same attachment?** — file hash already in Attachment table → flag as duplicate
3. **Same bill?** — vendor + bill number + bill date already in Bill table → flag as duplicate

Duplicates do NOT stop the pipeline. They surface a warning to the user on the draft.

**Step 4: Resolve**
Match extracted data to existing entities in the database:
- **Vendor** — match sender/vendor name to Vendor record
- **Date** — validate bill date is reasonable
- **Number** — validate bill number format
- **Project** — determine project from email body, PO number, ship-to address, or vendor history
- **Amount** — validate total matches line item math

Some fields will resolve with high confidence, some won't. Unresolved fields are left for user completion.

**Step 5: Create Draft Bill**
Create the draft bill in the system with:
- All resolved fields populated
- Source PDF attached to the bill line item (uploaded to Azure Blob → Attachment record → BillLineItemAttachment link)
- Duplicate warnings if flagged in Step 3

**Step 6: Route Based on Confidence**

Three high-importance fields gate auto-submission:
- Vendor matched (high)
- Bill number extracted (high)
- Project matched (high — required for PM routing)
- Amount extracted (medium — does not block auto-submission)

Routing:
- **All three high-importance fields resolved** →
  - Set bill status to "pending_review" (in-app status for review queue)
  - Forward email to project PM(s) with bill details (vendor, amount, bill number)
  - PM reply feeds back into "Existing thread" path at top
- **Any high-importance field missing** →
  - Flag for user to complete manually before it can be submitted

### After PM Approval (Phase 2+)
The agent will eventually own the full lifecycle beyond PM approval — bill finalization, SharePoint upload, Excel sync, QBO sync, and more. Phase 1 stops at routing.

### Current Trigger
Manual: user tags email with "Blue category" in Outlook. The `process_category_queue()` scheduler (currently stubbed) will automate this when rebuilt.
