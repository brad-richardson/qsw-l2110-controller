from __future__ import annotations

import hashlib
import json
import ssl
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlparse

import httpx

from qsw_l2110.errors import ApiError, AuthenticationError


class QswL2110Client:
    """Small client for the private web handlers embedded in QSS 2.2.x.

    The interface was derived from the QSW-L2110 2.2.3 firmware image. It is
    not a public QNAP API and must be verified against real hardware.
    """

    def __init__(
        self,
        base_url: str,
        *,
        verify: bool | ssl.SSLContext = True,
        timeout: float = 10.0,
        allow_http: bool = False,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        normalized = base_url.rstrip("/")
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("host must be a complete http:// or https:// URL")
        if parsed.scheme != "https" and not allow_http:
            raise ValueError("refusing clear-text login; use HTTPS or explicitly allow HTTP")
        self._client = httpx.Client(
            base_url=normalized,
            verify=verify,
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
            transport=transport,
            headers={"Accept": "application/json"},
        )

    def __enter__(self) -> QswL2110Client:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def authenticate(self, username: str, password: str) -> None:
        # These digests are replayable credentials. Never include the resulting
        # URL in logs or exception messages.
        params = {
            "loginusr": hashlib.md5(username.encode(), usedforsecurity=False).hexdigest(),
            "loginpwd": hashlib.md5(password.encode(), usedforsecurity=False).hexdigest(),
        }
        try:
            response = self._client.get("/authorize", params=params)
        except httpx.HTTPError as exc:
            raise AuthenticationError(
                "login request failed; credential digests were redacted"
            ) from exc
        if response.status_code >= 400:
            raise AuthenticationError(f"login failed with HTTP {response.status_code}")
        if "session" not in self._client.cookies:
            raise AuthenticationError("login response did not establish a session cookie")
        try:
            body = response.json()
        except json.JSONDecodeError:
            body = None
        if isinstance(body, dict) and str(body.get("redirect", "")).endswith("/login.html"):
            raise AuthenticationError("switch redirected the session back to login")

    def get_identity(self) -> dict[str, Any]:
        model = self.get_json("/get_model_name.json")
        status = self.get_json("/status.json")
        return {"model": model, "status": status}

    def get_lag_config(self) -> dict[str, Any]:
        return self.get_json("/port_trunk_cfg.json")

    def get_lag_status(self) -> dict[str, Any]:
        return self.get_json("/port_trunk_refresh.json")

    def get_port_pvids(self) -> dict[str, Any]:
        return self.get_json("/all_port_pvid.json")

    def get_vlans(self) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        try:
            with self._client.stream(
                "GET", "/tag_vlan.json", headers={"Accept": "text/event-stream"}
            ) as response:
                self._raise_for_status(response)
                for line in response.iter_lines():
                    # Parse incrementally so already-received events survive the
                    # firmware's timeout/connection-close completion behavior.
                    events.extend(parse_sse_lines((line,)))
        except httpx.ReadTimeout as exc:
            # The firmware UI treats an SSE disconnect as end-of-list. A timeout
            # after data is equivalent for this experimental client.
            if not events:
                raise ApiError("VLAN event stream timed out before returning data") from exc
        vlans = [event for event in events if "vlan_id" in event]
        if not vlans:
            raise ApiError("VLAN event stream contained no VLAN entries")
        return vlans

    def set_lag_config(self, payload: dict[str, Any]) -> None:
        self.post_json("/port_trunk_cfg.json", payload)

    def set_vlans(self, payload: dict[str, Any]) -> None:
        self.post_json("/tag_vlan.json", payload)

    def save(self) -> None:
        self.post_empty("/save_all_configs.json")

    def download_backup(self) -> bytes:
        try:
            response = self._client.get("/config/download")
        except httpx.HTTPError as exc:
            raise ApiError("configuration backup request failed") from exc
        self._raise_for_status(response)
        if _looks_like_html(response.content):
            raise AuthenticationError("configuration backup returned HTML instead of a file")
        if _content_is_login_redirect(response.content):
            raise AuthenticationError("configuration backup was redirected to login")
        if not response.content:
            raise ApiError("configuration backup was empty")
        return response.content

    def get_json(self, path: str) -> dict[str, Any]:
        try:
            response = self._client.get(path)
        except httpx.HTTPError as exc:
            raise ApiError(f"GET {path} failed") from exc
        self._raise_for_status(response)
        if _looks_like_html(response.content):
            raise AuthenticationError(f"GET {path} returned HTML; the session may have expired")
        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise ApiError(f"GET {path} returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise ApiError(f"GET {path} returned a non-object JSON value")
        if _is_login_redirect(data):
            raise AuthenticationError(f"GET {path} was redirected to login")
        return data

    def post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        try:
            response = self._client.post(path, json=payload)
        except httpx.HTTPError as exc:
            raise ApiError(f"POST {path} failed") from exc
        self._raise_for_status(response)
        if not response.content:
            return None
        if _looks_like_html(response.content):
            raise AuthenticationError(f"POST {path} returned HTML; the session may have expired")
        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise ApiError(f"POST {path} returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise ApiError(f"POST {path} returned a non-object JSON value")
        if _is_login_redirect(data):
            raise AuthenticationError(f"POST {path} was redirected to login")
        return data

    def post_empty(self, path: str) -> dict[str, Any] | None:
        try:
            response = self._client.post(path)
        except httpx.HTTPError as exc:
            raise ApiError(f"POST {path} failed") from exc
        self._raise_for_status(response)
        if not response.content:
            return None
        if _looks_like_html(response.content):
            raise AuthenticationError(f"POST {path} returned HTML; the session may have expired")
        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise ApiError(f"POST {path} returned invalid JSON") from exc
        if isinstance(data, dict) and _is_login_redirect(data):
            raise AuthenticationError(f"POST {path} was redirected to login")
        return data if isinstance(data, dict) else None

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code == 401:
            raise AuthenticationError("switch rejected the session")
        if response.status_code >= 400:
            raise ApiError(f"switch returned HTTP {response.status_code}")


def parse_sse_lines(lines: Iterable[str]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in lines:
        if not line.startswith("data:"):
            continue
        raw = line.removeprefix("data:").strip()
        if not raw:
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ApiError("VLAN event stream contained invalid JSON") from exc
        if isinstance(value, dict):
            events.append(value)
    return events


def _looks_like_html(content: bytes) -> bool:
    prefix = content.lstrip()[:32].lower()
    return prefix.startswith(b"<!doctype html") or prefix.startswith(b"<html")


def _content_is_login_redirect(content: bytes) -> bool:
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return False
    return isinstance(data, dict) and _is_login_redirect(data)


def _is_login_redirect(data: dict[str, Any]) -> bool:
    redirect = str(data.get("redirect", ""))
    return redirect.endswith("/login.html") or "/login.html?" in redirect
