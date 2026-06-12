# Python Standard Library Imports
import logging
from typing import Any, MutableMapping, Tuple

# Local Imports
from integrations.box.base.correlation import get_correlation_id


class BoxContextAdapter(logging.LoggerAdapter):
    """
    LoggerAdapter that auto-injects the current Box correlation ID into every
    log record's `extra` dict, pulled from the `box_correlation_id` ContextVar.

    Call sites emit structured events without threading `correlation_id` as a
    kwarg — the adapter merges it in automatically:

        logger = get_box_logger(__name__)
        logger.info("box.foo.bar", extra={"event_name": "box.foo.bar", ...})
        # correlation_id is added if the current context has one

    If no correlation ID is set in context, the record has no correlation_id
    field (rather than a sentinel) — downstream consumers should treat it as
    "not available" rather than a distinct value.
    """

    def process(
        self,
        msg: Any,
        kwargs: MutableMapping[str, Any],
    ) -> Tuple[Any, MutableMapping[str, Any]]:
        extra = kwargs.setdefault("extra", {})
        # Never overwrite a caller-supplied correlation_id — the caller may
        # have a more specific value from elsewhere in their flow.
        if "correlation_id" not in extra:
            current = get_correlation_id()
            if current is not None:
                extra["correlation_id"] = current
        return msg, kwargs


def get_box_logger(name: str) -> BoxContextAdapter:
    """
    Return a BoxContextAdapter wrapping `logging.getLogger(name)`.

    Use this in place of `logging.getLogger(__name__)` anywhere in
    `integrations/box/` that emits events expected to carry correlation IDs.
    All callers live under the `integrations.box` logger namespace, so
    operators can tune verbosity for the whole integration in one place.
    """
    return BoxContextAdapter(logging.getLogger(name), {})
