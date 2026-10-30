"""U-535b b2c: the web app calls the API cross-origin, so browsers hide custom
response headers unless CORS exposes them. `X-Content-Matches-Check` must be
listed in `Access-Control-Expose-Headers`, without disturbing the existing
credentialed-origin behaviour."""

from fastapi.testclient import TestClient

import app as app_module

EXPOSED = "X-Content-Matches-Check"


def _origin() -> str:
    # Use whatever the app actually allows (env override or the defaults).
    return app_module._allowed_origins[0]


def _get_ping(origin: str):
    client = TestClient(app_module.app)
    return client.get("/ping", headers={"Origin": origin})


def test_simple_request_exposes_content_matches_check_header():
    resp = _get_ping(_origin())
    assert resp.status_code == 200
    exposed = resp.headers.get("access-control-expose-headers", "")
    names = [h.strip().lower() for h in exposed.split(",") if h.strip()]
    assert EXPOSED.lower() in names, exposed


def test_existing_cors_behaviour_preserved():
    origin = _origin()
    resp = _get_ping(origin)
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == origin
    assert resp.headers.get("access-control-allow-credentials") == "true"


def test_disallowed_origin_gets_no_cors_headers():
    resp = _get_ping("https://evil.example.com")
    assert resp.status_code == 200
    # Starlette attaches Expose-Headers / Allow-Credentials to every Origin-bearing
    # response; harmless — without Allow-Origin the browser blocks the read entirely.
    assert "access-control-allow-origin" not in resp.headers
