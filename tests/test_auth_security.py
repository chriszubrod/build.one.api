import base64
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import bcrypt
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient

from services.auth.business.model import Auth, AuthToken, RefreshToken
from services.auth.business.service import AuthService, get_current_user_api
import services.auth.api.router as auth_router_module


DEFAULT_ROW_VERSION = base64.b64encode(b"\x00" * 8).decode("ascii")


def _make_auth(*, public_id: str, password_hash: str, username: str = "user") -> Auth:
    return Auth(
        id=1,
        public_id=public_id,
        row_version=DEFAULT_ROW_VERSION,
        created_datetime="2024-01-01T00:00:00Z",
        modified_datetime="2024-01-01T00:00:00Z",
        username=username,
        password_hash=password_hash,
        user_id=1,
    )


class StubAuthRepo:
    def __init__(self):
        self.created = None
        self.updated = None
        self.old_hash = None

    def read_by_username(self, username: str):
        return None

    def create(self, *, username: str, password_hash: str):
        self.created = {"username": username, "password_hash": password_hash}
        return _make_auth(public_id="pub", password_hash=password_hash, username=username)

    def read_by_public_id(self, public_id: str):
        return _make_auth(public_id=public_id, password_hash=self.old_hash or "oldhash")

    def update_by_id(self, auth: Auth):
        self.updated = auth
        return auth


class StubAuthService:
    def __init__(self):
        self.create_calls = 0
        self.last_create = None
        self.read_by_public_id_calls = 0
        self.update_by_public_id_calls = 0
        self.update_user_id_by_public_id_calls = 0
        self.delete_by_public_id_calls = 0

    def create(self, *, username: str, password: str):
        self.create_calls += 1
        self.last_create = {"username": username, "password": password}
        return _make_auth(public_id="create-sub", password_hash="hashed", username=username)

    def read_by_public_id(self, public_id: str):
        self.read_by_public_id_calls += 1
        return _make_auth(public_id=public_id, password_hash="hashed")

    def update_by_public_id(self, public_id: str, auth):
        self.update_by_public_id_calls += 1
        return _make_auth(public_id=public_id, password_hash="hashed")

    def update_user_id_by_public_id(self, public_id: str, user_public_id: str):
        self.update_user_id_by_public_id_calls += 1
        return _make_auth(public_id=public_id, password_hash="hashed")

    def delete_by_public_id(self, public_id: str):
        self.delete_by_public_id_calls += 1
        return _make_auth(public_id=public_id, password_hash="hashed")

    def login(self, *, username: str, password: str):
        auth = _make_auth(public_id="login-sub", password_hash="hashed", username=username)
        token = AuthToken(access_token="access", token_type="Bearer", expires_in=3600)
        refresh = RefreshToken(refresh_token="refresh", token_type="Bearer", expires_in=7200)
        return auth, token, refresh

    def signup(self, *, username: str, password: str, confirm_password: str):
        auth = _make_auth(public_id="signup-sub", password_hash="hashed", username=username)
        token = AuthToken(access_token="access", token_type="Bearer", expires_in=3600)
        refresh = RefreshToken(refresh_token="refresh", token_type="Bearer", expires_in=7200)
        return auth, token, refresh

    def refresh_access_token(self, *, refresh_token: str):
        token = AuthToken(access_token="access", token_type="Bearer", expires_in=3600)
        refresh = RefreshToken(refresh_token="refresh", token_type="Bearer", expires_in=7200)
        return token, refresh

class AuthModelTests(unittest.TestCase):
    def test_to_dict_excludes_password_hash(self):
        auth = _make_auth(public_id="pub", password_hash="secret-hash")
        data = auth.to_dict()
        self.assertNotIn("password_hash", data)


class AuthServiceSecurityTests(unittest.TestCase):
    def test_create_hashes_password(self):
        repo = StubAuthRepo()
        service = AuthService(repo=repo)
        auth = service.create(username="user", password="supersecret")

        self.assertIsNotNone(repo.created)
        self.assertNotEqual(repo.created["password_hash"], "supersecret")
        self.assertTrue(
            bcrypt.checkpw(
                b"supersecret", repo.created["password_hash"].encode("utf-8")
            )
        )
        self.assertNotEqual(auth.password_hash, "supersecret")

    def test_update_hashes_password(self):
        repo = StubAuthRepo()
        repo.old_hash = bcrypt.hashpw(b"oldpass", bcrypt.gensalt()).decode("utf-8")
        service = AuthService(repo=repo)

        payload = SimpleNamespace(
            row_version=DEFAULT_ROW_VERSION,
            username="user",
            password="newpassword",
            user_id=10,
        )
        updated = service.update_by_public_id(public_id="pub", auth=payload)

        self.assertIsNotNone(repo.updated)
        self.assertTrue(
            bcrypt.checkpw(b"newpassword", repo.updated.password_hash.encode("utf-8"))
        )
        self.assertNotEqual(repo.updated.password_hash, repo.old_hash)
        self.assertEqual(updated.password_hash, repo.updated.password_hash)


class AuthApiSecurityTests(unittest.TestCase):
    def setUp(self):
        self._original_service = auth_router_module.service
        self._original_secure_cookie_enabled = auth_router_module._secure_cookie_enabled
        auth_router_module._secure_cookie_enabled = lambda: False

    def tearDown(self):
        auth_router_module.service = self._original_service
        auth_router_module._secure_cookie_enabled = self._original_secure_cookie_enabled

    def _make_client(self, sub: str):
        app = FastAPI()
        app.include_router(auth_router_module.router)
        app.dependency_overrides[get_current_user_api] = lambda: {"sub": sub}
        return TestClient(app)

    def _make_client_no_auth(self):
        app = FastAPI()
        app.include_router(auth_router_module.router)
        return TestClient(app)

    def _get_set_cookies(self, response):
        headers = response.headers
        if hasattr(headers, "getlist"):
            return headers.getlist("set-cookie") or headers.getlist("Set-Cookie") or []
        if hasattr(headers, "get_all"):
            return headers.get_all("set-cookie") or headers.get_all("Set-Cookie") or []
        raw_headers = getattr(getattr(response, "raw", None), "headers", None)
        if raw_headers is not None:
            if hasattr(raw_headers, "get_all"):
                return raw_headers.get_all("Set-Cookie") or []
            if hasattr(raw_headers, "getlist"):
                return raw_headers.getlist("Set-Cookie") or []
        header = headers.get("set-cookie") or headers.get("Set-Cookie")
        if not header:
            return []
        return self._split_set_cookie_header(header)

    def _split_set_cookie_header(self, header: str):
        parts = []
        current = []
        in_expires = False
        i = 0
        header_len = len(header)
        while i < header_len:
            ch = header[i]
            if not in_expires and ch == ",":
                part = "".join(current).strip()
                if part:
                    parts.append(part)
                current = []
                i += 1
                if i < header_len and header[i] == " ":
                    i += 1
                continue
            current.append(ch)
            if not in_expires and header[i:i + 8].lower() == "expires=":
                in_expires = True
            elif in_expires and ch == ";":
                in_expires = False
            i += 1
        last = "".join(current).strip()
        if last:
            parts.append(last)
        return parts

    def _parse_set_cookie(self, cookie_value: str):
        parts = [part.strip() for part in cookie_value.split(";") if part.strip()]
        name, value = parts[0].split("=", 1)
        attrs = {}
        for part in parts[1:]:
            if "=" in part:
                key, val = part.split("=", 1)
                attrs[key.strip().lower()] = val.strip()
            else:
                attrs[part.strip().lower()] = True
        return {"name": name, "value": value, "attrs": attrs}

    def _get_cookie_map(self, response):
        cookies = self._get_set_cookies(response)
        parsed = [self._parse_set_cookie(cookie) for cookie in cookies]
        return {entry["name"]: entry for entry in parsed}

    def test_get_auth_self_only_forbidden(self):
        stub = StubAuthService()
        auth_router_module.service = stub
        client = self._make_client(sub="token-sub")

        response = client.get("/api/v1/get/auth/other-sub")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(stub.read_by_public_id_calls, 0)

    def test_get_auth_omits_password_hash(self):
        stub = StubAuthService()
        auth_router_module.service = stub
        client = self._make_client(sub="self-sub")

        response = client.get("/api/v1/get/auth/self-sub")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("password_hash", response.json())

    def test_update_auth_self_only_forbidden(self):
        stub = StubAuthService()
        auth_router_module.service = stub
        client = self._make_client(sub="token-sub")

        response = client.put(
            "/api/v1/update/auth/other-sub",
            json={
                "row_version": DEFAULT_ROW_VERSION,
                "username": "user",
                "password": "newpassword",
                "user_id": 1,
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(stub.update_by_public_id_calls, 0)

    def test_update_user_id_self_only_forbidden(self):
        stub = StubAuthService()
        auth_router_module.service = stub
        client = self._make_client(sub="token-sub")

        response = client.put(
            "/api/v1/update/auth/other-sub/user-public-id/user-123"
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(stub.update_user_id_by_public_id_calls, 0)

    def test_delete_auth_self_only_forbidden(self):
        stub = StubAuthService()
        auth_router_module.service = stub
        client = self._make_client(sub="token-sub")

        response = client.delete("/api/v1/delete/auth/other-sub")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(stub.delete_by_public_id_calls, 0)

    def test_create_auth_omits_password_hash(self):
        stub = StubAuthService()
        auth_router_module.service = stub
        client = self._make_client(sub="token-sub")

        response = client.post(
            "/api/v1/create/auth",
            json={"username": "user", "password": "password123"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(stub.create_calls, 1)
        self.assertEqual(stub.last_create, {"username": "user", "password": "password123"})
        self.assertNotIn("password_hash", response.json())

    def test_login_response_omits_password_hash(self):
        stub = StubAuthService()
        auth_router_module.service = stub
        client = self._make_client_no_auth()

        response = client.post(
            "/api/v1/auth/login",
            json={"username": "user", "password": "password123"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("auth", data)
        self.assertNotIn("password_hash", data["auth"])

    def test_login_sets_auth_and_csrf_cookies(self):
        stub = StubAuthService()
        auth_router_module.service = stub
        client = self._make_client_no_auth()

        response = client.post(
            "/api/v1/auth/login",
            json={"username": "user", "password": "password123"},
        )
        self.assertEqual(response.status_code, 200)
        cookie_map = self._get_cookie_map(response)
        self.assertIn("token.access_token", cookie_map)
        self.assertIn("token.refresh_token", cookie_map)
        self.assertIn("token.csrf", cookie_map)

        access_attrs = cookie_map["token.access_token"]["attrs"]
        refresh_attrs = cookie_map["token.refresh_token"]["attrs"]
        csrf_attrs = cookie_map["token.csrf"]["attrs"]

        self.assertIn("httponly", access_attrs)
        self.assertIn("httponly", refresh_attrs)
        self.assertNotIn("httponly", csrf_attrs)

        self.assertEqual(access_attrs.get("samesite"), "lax")
        self.assertEqual(refresh_attrs.get("samesite"), "lax")
        self.assertEqual(csrf_attrs.get("samesite"), "lax")

        self.assertEqual(access_attrs.get("path"), "/")
        self.assertEqual(refresh_attrs.get("path"), "/")
        self.assertEqual(csrf_attrs.get("path"), "/")

        self.assertNotIn("secure", access_attrs)
        self.assertNotIn("secure", refresh_attrs)
        self.assertNotIn("secure", csrf_attrs)

        self.assertEqual(int(access_attrs.get("max-age", 0)), 3600)
        self.assertEqual(int(refresh_attrs.get("max-age", 0)), 7200)
        self.assertEqual(int(csrf_attrs.get("max-age", 0)), 7200)

    def test_refresh_requires_csrf_with_cookie(self):
        stub = StubAuthService()
        auth_router_module.service = stub
        client = self._make_client_no_auth()
        client.cookies.set("token.refresh_token", "refresh")
        client.cookies.set("token.csrf", "csrf")

        response = client.post("/api/v1/auth/refresh")
        self.assertEqual(response.status_code, 403)

        response = client.post(
            "/api/v1/auth/refresh",
            headers={"X-CSRF-Token": "csrf"},
        )
        self.assertEqual(response.status_code, 200)

    def test_refresh_allows_body_without_csrf(self):
        stub = StubAuthService()
        auth_router_module.service = stub
        client = self._make_client_no_auth()

        response = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": "refresh"},
        )
        self.assertEqual(response.status_code, 200)

    def test_signup_response_omits_password_hash(self):
        stub = StubAuthService()
        auth_router_module.service = stub
        client = self._make_client_no_auth()

        response = client.post(
            "/api/v1/signup/auth",
            json={
                "username": "user",
                "password": "password123",
                "confirm_password": "password123",
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("auth", data)
        self.assertNotIn("password_hash", data["auth"])


class AuthCsrfDependencyTests(unittest.TestCase):
    def _make_app(self):
        app = FastAPI()

        @app.post("/protected")
        def protected(current_user: dict = Depends(get_current_user_api)):
            return {"ok": True}

        return app

    def test_cookie_auth_requires_csrf(self):
        app = self._make_app()
        client = TestClient(app)
        with patch("services.auth.business.service.verify_token", return_value={"sub": "user"}):
            client.cookies.set("token.access_token", "access")
            client.cookies.set("token.csrf", "csrf")
            response = client.post("/protected")
            self.assertEqual(response.status_code, 403)
            response = client.post("/protected", headers={"X-CSRF-Token": "csrf"})
            self.assertEqual(response.status_code, 200)

    def test_header_auth_allows_no_csrf(self):
        app = self._make_app()
        client = TestClient(app)
        with patch("services.auth.business.service.verify_token", return_value={"sub": "user"}):
            response = client.post("/protected", headers={"Authorization": "Bearer access"})
            self.assertEqual(response.status_code, 200)
