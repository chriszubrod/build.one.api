"""
Shared line-attachment push (U-797c5a round 2, R4).

Bill's push_to_qbo and BillCredit's VendorCredit push upload each line's attachment to the
QBO document as an Attachable. The walk is identical; only the entity type and the reads differ.
The attachable connector is entity-generic: `entity_type` links the upload to the parent.

Dedupe semantics (Bill's, kept): an attachment joins the synced set only on success, so a
shared attachment that fails on one line is attempted again on the next and reported again.
Budget and write-refusal are re-raised so the worker parks the row.
"""

from integrations.intuit.qbo.base.errors import QboBudgetExceededError, QboWriteRefusedError


def sync_line_attachments(
    *,
    line_items,
    read_link,
    read_attachment,
    connector,
    entity_type: str,
    qbo_entity_id,
    realm_id: str,
    logger,
    label: str,
) -> tuple:
    """
    Upload each line's attachment to the QBO entity. `read_link(line_public_id)` returns the
    line's attachment link (with `attachment_id`), `read_attachment(attachment_id)` the Attachment.
    Returns (attachments_synced, errors).
    """
    attachments_synced = 0
    errors = []
    synced_attachment_ids = set()

    for line_item in line_items:
        if not line_item.public_id:
            continue

        attachment_link = read_link(line_item.public_id)
        if not attachment_link or not attachment_link.attachment_id:
            continue

        # Skip if already synced (same attachment shared across line items)
        if attachment_link.attachment_id in synced_attachment_ids:
            continue

        attachment = read_attachment(attachment_link.attachment_id)
        if not attachment or not attachment.blob_url:
            continue

        try:
            connector.sync_attachment_to_qbo(
                attachment=attachment,
                realm_id=realm_id,
                entity_type=entity_type,
                entity_id=qbo_entity_id,
            )
            synced_attachment_ids.add(attachment_link.attachment_id)
            attachments_synced += 1
        except QboBudgetExceededError:
            # U-211: a tripped monthly budget is environmental; propagate so the worker parks.
            raise
        except QboWriteRefusedError:
            # U-218e: refused locally, no bytes sent; propagate so the worker parks.
            raise
        except Exception as e:
            error_msg = f"Attachment {attachment.id} sync failed: {str(e)}"
            logger.error(f"Failed to sync attachment {attachment.id} to QBO for {label}: {e}")
            errors.append(error_msg)

    if attachments_synced > 0:
        logger.info(f"Synced {attachments_synced} attachment(s) to QBO for {label}")

    return attachments_synced, errors
