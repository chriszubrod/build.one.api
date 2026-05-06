# Runbook: Review Notification Failed

A draft Bill was submitted for review (auto-Submit hook fired) but the
expected notification didn't land in `invoice@rogersbuild.com`'s Drafts
folder — or it landed but with wrong recipients / missing attachment.

## Symptom

Any of:

- A new draft Bill appears in the system but no draft email shows up in
  `invoice@rogersbuild.com`'s Drafts folder within ~60s.
- Draft email is in the Drafts folder but To: / Cc: lines are empty when
  reviewers should be assigned, or attachment is missing.
- `[ms].[Outbox]` rows with `Kind='send_mail'` accumulate in `Status` of
  `failed` or `dead_letter`.
- Log lines `review_notification.skipped` or
  `review_notification.unreachable_recipients` are firing more than expected.
- Operator reports "I'm not getting review emails for bills I should be
  reviewing."

## Severity

| Condition | Severity | Expected response |
|---|---|---|
| Single bill missing notification | Warning | Diagnose; recover the one row |
| Multiple bills, same project | Warning | Likely a `UserProject.RoleId` config issue on that project |
| Multiple bills, all projects | Critical | Check `invoice_inbox_email` config, MS auth health, outbox worker health |
| `send_mail` dead-letter count > 0 over 1 hour | High | Use retry script after diagnosing |

## Background

The review-submit notification pipeline (Wave 4, May 2026) lives in
`entities/review/business/notification_service.py`. Trigger flow:

1. `BillService.create()` writes a Bill row.
2. Auto-Submit hook writes a `Review` row at first ReviewStatus
   ("Submitted").
3. `ReviewNotificationService.enqueue_for_bill(bill, review, exclude_user_id)`
   resolves recipients via `dbo.ResolveReviewRecipientsByBillId`, fetches
   the source-summary PDF from Azure blob, and enqueues an
   `[ms].[Outbox]` row with `Kind='send_mail'`.
4. The MS outbox worker drains the row (~5-30s) and calls
   `create_draft` (mode=draft) or `send_message` (mode=send) against
   Graph.

Notification failure NEVER rolls back the Bill or the Review row — the
hook is failure-isolated. Recovery is to re-enqueue or fix config.

## Immediate action

None usually — the bill itself is intact. The notification is a layer on
top. Only urgency is if reviewers are unaware that bills are queued.

## Diagnosis

Replace `<BILL_ID>` with the actual `dbo.Bill.Id` you're investigating.

1. **Confirm the Review row exists.**
   ```sql
   SELECT TOP 1 [Id], [UserId], [ReviewStatusId],
          CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS Created
   FROM dbo.[Review]
   WHERE [BillId] = <BILL_ID>
   ORDER BY [Id] DESC;
   ```
   No row → the auto-Submit hook itself didn't fire. Check
   `is_draft=True` and `user_id` was non-NULL when `BillService.create`
   was called. Look for `Failed to auto-write Submitted Review row`
   in App Insights.

2. **Check whether an outbox row was enqueued for this bill.**
   ```sql
   SELECT TOP 5 [PublicId], [Status], [Attempts], [LastError],
          CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS Created,
          [Payload]
   FROM [ms].[Outbox]
   WHERE [Kind] = 'send_mail'
     AND [EntityType] = 'Bill'
     AND [EntityPublicId] = '<BILL_PUBLIC_ID>'
   ORDER BY [Id] DESC;
   ```
   - **No row** → recipient resolver returned empty AND BCC archive was
     unconfigured (see step 3) OR the notification service threw early
     and was swallowed by its outer try/except. Check App Insights for
     `review_notification.enqueue_failed`,
     `review_notification.skipped`,
     `review_notification.bcc_archive_skipped`.
   - **`Status='pending'`** → not drained yet; wait ≤30s for the
     scheduler tick.
   - **`Status='failed'`** → retry pending; check `LastError`.
   - **`Status='dead_letter'`** → exhausted; recover via retry script
     (see Recovery).
   - **`Status='done'`** → succeeded. Check Drafts folder + verify
     recipient lines.

3. **Run the resolver against the bill.**
   ```python
   from entities.review.business.recipient_service import ReviewRecipientService
   r = ReviewRecipientService().resolve_for_bill(bill_id=<BILL_ID>, exclude_user_id=None)
   print(r)
   ```
   Empty `to` and `cc` → no `UserProject` rows tagged
   `RoleId='Project Manager'` or `'Owner'` exist for any project the
   bill spans. Confirm via:
   ```sql
   SELECT DISTINCT bli.[ProjectId] FROM dbo.[BillLineItem] bli
   WHERE bli.[BillId] = <BILL_ID> AND bli.[ProjectId] IS NOT NULL;
   -- For each ProjectId returned:
   SELECT up.[UserId], r.[Name] AS RoleName, c.[Email]
   FROM dbo.[UserProject] up
   INNER JOIN dbo.[Role] r ON r.[Id] = up.[RoleId]
   LEFT JOIN dbo.[Contact] c ON c.[UserId] = up.[UserId]
   WHERE up.[ProjectId] = <PROJECT_ID>
     AND r.[Name] IN ('Project Manager', 'Owner');
   ```

4. **Check `invoice_inbox_email` is configured.**
   ```python
   from config import Settings
   print(Settings().invoice_inbox_email)
   ```
   Should be `invoice@rogersbuild.com` in prod. If `None`, the BCC
   archive was skipped — log line `review_notification.bcc_archive_skipped`
   would have fired.

5. **Check `REVIEW_NOTIFICATION_MODE`.**
   ```python
   print(Settings().review_notification_mode)
   ```
   Default is `draft`. If `send`, drafts won't land in Drafts folder —
   they go directly via `me/sendMail`.

## Common causes

Ranked roughly by likelihood:

1. **No `UserProject` row with `RoleId` set on the project.** During
   early rollout most projects lack PM/Owner role assignments — the
   resolver returns empty TO/CC, only BCC archive is populated.
   Reviewers don't get the email even though invoice@ does.
2. **Reviewer User has no `Contact` row with an email.** Resolver
   returns the User in the result list but with `Email=NULL`; service
   filters them and logs `review_notification.unreachable_recipients`.
3. **Bill has no project on its line items.** The auto-created summary
   line in `BillService.create` should populate `ProjectId` from
   `line_project_public_id`, but human-created drafts can leave it
   blank. Resolver finds zero candidate projects → zero TO/CC.
4. **Outbox row dead-lettered after 5 retry attempts.** Sustained Graph
   issue or a malformed payload (e.g., attachment too large).
5. **`invoice_inbox_email` env var unset on App Service.** New
   environment, recent restore, etc.
6. **MS auth token expired and refresh failed.** Cross-reference
   [ms-token-expiration.md](ms-token-expiration.md).

## Recovery

### Cause 1: missing UserProject role assignment

Insert/update the relevant `UserProject` row(s):

```sql
-- Example: assign User 17 as Project Manager on Project 64
DECLARE @PMRoleId BIGINT = (SELECT [Id] FROM dbo.[Role] WHERE [Name] = 'Project Manager');
IF NOT EXISTS (SELECT 1 FROM dbo.[UserProject] WHERE [UserId] = 17 AND [ProjectId] = 64)
    EXEC dbo.CreateUserProject @UserId = 17, @ProjectId = 64, @RoleId = @PMRoleId;
ELSE
    UPDATE dbo.[UserProject] SET [RoleId] = @PMRoleId
    WHERE [UserId] = 17 AND [ProjectId] = 64;
```

The notification fires only on **first-time** Bill submission, so
fixing this won't retroactively notify reviewers about already-submitted
bills. To force a retry for one bill:

```python
from entities.review.business.notification_service import ReviewNotificationService
from entities.bill.business.service import BillService
from entities.review.business.service import ReviewService
bill = BillService().read_by_id(<BILL_ID>)
review = ReviewService().repo.read_current_by_bill_id(<BILL_ID>)
ReviewNotificationService().enqueue_for_bill(bill=bill, review=review, exclude_user_id=review.user_id)
```

### Cause 2: missing Contact email

Add a Contact row for the User:

```sql
DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();
INSERT INTO dbo.[Contact]([CreatedDatetime], [ModifiedDatetime], [Email], [UserId])
VALUES (@Now, @Now, '<email@rogersbuild.com>', <USER_ID>);
```

Then re-trigger the notification per Cause 1.

### Cause 4: dead-lettered outbox rows

```bash
# Dry-run first
python scripts/retry_ms_outbox_dead_letters.py --kind send_mail
# Apply
python scripts/retry_ms_outbox_dead_letters.py --kind send_mail --apply
```

### Cause 5: missing env var

Set on App Service:
```bash
az webapp config appsettings set --name buildone --resource-group buildone_group \
   --settings invoice_inbox_email=invoice@rogersbuild.com
az webapp restart --name buildone --resource-group buildone_group
```

## Verification

1. Re-fire the notification (Cause 1 example above).
2. Watch the row through `pending → done`:
   ```sql
   SELECT [Status], [Attempts], [LastError]
   FROM [ms].[Outbox]
   WHERE [Kind] = 'send_mail' AND [EntityPublicId] = '<BILL_PUBLIC_ID>'
   ORDER BY [Id] DESC;
   ```
3. Check `invoice@rogersbuild.com`'s Drafts folder for a new draft with
   the expected subject (`{ProjectAbbrev} - {Vendor} - {Number} - {Amount}`).
4. Confirm To: line lists the expected PM(s); BCC line lists
   `invoice@rogersbuild.com`.

## Prevention

- During React UI development, prioritize the surface that lets admins
  assign `UserProject.RoleId` per project. v1 has no UI; rows must be
  populated via SQL — easy to forget on a new project.
- Add an admin smoke check that runs daily and counts active draft
  Projects with at least one Bill but zero PM/Owner UserProject rows.
- Encourage every new User to populate their Contact email at signup.
