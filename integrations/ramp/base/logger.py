# Python Standard Library Imports
import logging
from typing import Any, MutableMapping, Tuple

# Local Imports
from integrations.ramp.base.correlation import get_correlation_id


class RampContextAdapter(logging.LoggerAdapter):
    def process(
        self,
        msg: Any,
        kwargs: MutableMapping[str, Any],
    ) -> Tuple[Any, MutableMapping[str, Any]]:
        extra = kwargs.setdefault("extra", {})
        if "correlation_id" not in extra:
            current = get_correlation_id()
            if current is not None:
                extra["correlation_id"] = current
        return msg, kwargs


def get_ramp_logger(name: str) -> RampContextAdapter:
    return RampContextAdapter(logging.getLogger(name), {})
