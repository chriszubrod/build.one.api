"""Transport-layer HTTP errors with an additive machine-readable `error_code`.

Wire contract (U-357d — forward-compat for the iOS offline client): every HTTP
error body is `{"detail": <unchanged>, "error_code": <str | null>}`. The key is a
SIBLING of `detail`, never nested inside it — installed clients decode
`{detail: String?}` and substring-match the detail text for offline-queue
recovery, so `detail` and status codes are frozen; only the sibling is new.

`ErrorCode` is the one Python source of truth for the literals the iOS/web
clients key on. Later lifecycle phases append here (`status_locked`,
`status_conflict`, `review_open`, `review_declined`, `approval_required`,
`lines_uncoded`, `review_status_shape`) rather than scattering string literals.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.exception_handlers import http_exception_handler as _fastapi_http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.utils import is_body_allowed_for_status_code
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request

from shared.access import EntityNotAccessibleError


class ErrorCode:
    """Machine-readable codes carried beside `detail`.

    Plain `str` constants (same shape as `shared.rbac_constants.Modules`): a typo
    is an `AttributeError` at import time, not a silent client fallback to the
    substring rule.
    """

    ENTRY_LOCKED = "entry_locked"  # parent entry left draft — only drafts accept edits
    TRANSITION_INVALID = "transition_invalid"  # "Cannot transition from … to …"
    DUPLICATE_KEY = "duplicate_key"  # unique-key violation (2627/2601)
    FK_VIOLATION = "fk_violation"  # foreign-key violation (547)
    NOT_FOUND = "not_found"
    VALIDATION_ERROR = "validation_error"  # request body/query/path failed schema validation


class ApiError(HTTPException):
    """`HTTPException` that also carries `error_code`."""

    def __init__(
        self,
        status_code: int,
        detail: object = None,
        error_code: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.error_code = error_code


def error_response(
    status_code: int,
    detail: object,
    error_code: str | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """The single builder of the error body shape — every error body goes through here."""
    return JSONResponse(
        status_code=status_code,
        headers=headers,
        content={"detail": detail, "error_code": error_code},
    )


async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    # Registered on the Starlette base class so it covers fastapi.HTTPException
    # (a subclass — every raise_* helper and HTTPBearer's 403) AND the routing
    # 404/405 Starlette raises itself. No-body statuses (204/304) defer to
    # FastAPI's own handler so they never grow a JSON body.
    if not is_body_allowed_for_status_code(exc.status_code):
        return await _fastapi_http_exception_handler(request, exc)
    return error_response(
        exc.status_code,
        exc.detail,
        getattr(exc, "error_code", None),
        getattr(exc, "headers", None),
    )


async def request_validation_handler(request: Request, exc: RequestValidationError):
    # Byte-identical to FastAPI's default (422 + jsonable_encoder(exc.errors()) as
    # `detail`), plus the sibling key — so the contract holds for every error body.
    return error_response(422, jsonable_encoder(exc.errors()), ErrorCode.VALIDATION_ERROR)


async def entity_not_accessible_handler(request: Request, exc: EntityNotAccessibleError):
    """Map per-row access denial to 404 (not 403) so the URL doesn't confirm
    the entity exists to a caller without UserProject access."""
    return error_response(404, "Not found", ErrorCode.NOT_FOUND)


def install_error_handlers(app: FastAPI) -> None:
    """Register every error-body producer on `app` (one module owns the shape)."""
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, request_validation_handler)
    app.add_exception_handler(EntityNotAccessibleError, entity_not_accessible_handler)
