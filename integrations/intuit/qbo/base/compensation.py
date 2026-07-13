"""Best-effort compensating rollback when QBO->dbo line projection fails after header create."""
import logging

logger = logging.getLogger(__name__)


def rollback_orphan_header(*, delete_header, delete_mapping, entity_label, entity_id):
    """Best-effort compensating delete of a just-created header + its QBO mapping after a
    line-sync failure, so a permanent per-line failure never strands a header-only 'zombie'.
    Each delete is isolated in its own try/except: failures are LOGGED, never raised, so the
    caller's ORIGINAL line-sync exception propagates unchanged (the pull watermark holds and
    the next idempotent re-pull rebuilds the entity cleanly). delete_header and delete_mapping
    are zero-arg callables supplied by the connector."""
    try:
        delete_header()
    except Exception as e:
        logger.error(f'Compensating rollback: failed to delete orphan {entity_label} header {entity_id}: {e}')
    try:
        delete_mapping()
    except Exception as e:
        logger.error(f'Compensating rollback: failed to delete {entity_label} mapping for {entity_id}: {e}')
