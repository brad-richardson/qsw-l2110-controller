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
    """Low-level client for the private web handlers embedded in QSS 2.2.x.

    The interface was derived from the QSW-L2110 2.2.3 firmware image. It is
    not a public QNAP API and must be verified against real hardware. Direct
    mutating calls bypass the CLI's identity, backup, planning, ordering, and
    read-back safety gates; use them only for controlled protocol research.
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
        if (
            parsed.path not in {"", "/"}
            or parsed.params
            or parsed.query
            or parsed.fragment
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("host URL must contain only scheme, hostname, and optional port")
        if parsed.scheme != "https" and not allow_http:
            raise ValueError("refusing clear-text login; use HTTPS or explicitly allow HTTP")
        self._client = httpx.Client(
            base_url=normalized,
            verify=verify,
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
            # QSS responds to /authorize without Content-Length or
            # Connection: close, then drops the socket. A pooled keep-alive
            # connection reused for the next request fails with a
            # RemoteProtocolError, so every request asks for a fresh socket.
            headers={"Accept": "application/json", "Connection": "close"},
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
        except httpx.HTTPError:
            raise AuthenticationError(
                "login request failed; credential digests were redacted"
            ) from None
        if not 200 <= response.status_code < 300:
            raise AuthenticationError(f"login failed with HTTP {response.status_code}")
        if "session" not in self._client.cookies:
            raise AuthenticationError("login response did not establish a session cookie")
        try:
            body = response.json()
        except json.JSONDecodeError:
            body = None
        if isinstance(body, dict) and _is_login_redirect(body):
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

    def get_vlan_ids(self) -> dict[str, Any]:
        return self.get_json("/get_vlan_list.json")

    def get_vlan_snapshot(
        self, *, attempts: int = 2
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Read a complete VLAN inventory bracketed by stable PVID metadata."""
        for _attempt in range(attempts):
            before_ids = _vlan_list_signature(self.get_vlan_ids())
            before_pvids = _pvid_signature(self.get_port_pvids())
            vlans = self.get_vlans()
            after_pvid_data = self.get_port_pvids()
            after_pvids = _pvid_signature(after_pvid_data)
            after_ids = _vlan_list_signature(self.get_vlan_ids())
            sse_ids = _vlan_ids(vlans)
            if (
                before_ids == after_ids == sse_ids
                and before_pvids == after_pvids
                and set(after_pvids).issubset(sse_ids)
            ):
                return vlans, after_pvid_data
        raise ApiError("VLAN inventory changed or was incomplete during snapshot")

    def get_vlans(self) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        try:
            with self._client.stream(
                "GET", "/tag_vlan.json", headers={"Accept": "text/event-stream"}
            ) as response:
                self._raise_for_status(response)
                for line in response.iter_lines():
                    # Parse incrementally while requiring a clean EOF below.
                    events.extend(parse_sse_lines((line,)))
        except httpx.ReadTimeout as exc:
            # A normal firmware completion produces EOF. A timeout is ambiguous:
            # accepting already-received events could hide VLANs and defeat the
            # unmanaged-VLAN safety checks.
            raise ApiError("VLAN event stream timed out before cleanly closing") from exc
        except httpx.HTTPError as exc:
            raise ApiError("VLAN event stream failed before cleanly closing") from exc
        metadata = [event for event in events if "PortNum" in event]
        if len(metadata) != 1 or events[0] is not metadata[0]:
            raise ApiError("VLAN event stream did not start with exactly one PortNum event")
        try:
            port_count = _nonnegative_integer(metadata[0]["PortNum"])
        except ValueError as exc:
            raise ApiError("VLAN event stream returned an invalid PortNum") from exc
        if port_count != 10:
            raise ApiError(f"VLAN event stream reported {port_count} ports; expected 10")
        vlans = [event for event in events if "vlan_id" in event]
        if not vlans:
            raise ApiError("VLAN event stream contained no VLAN entries")
        _vlan_ids(vlans)
        return vlans

    def set_lag_config(self, payload: dict[str, Any]) -> None:
        self.post_json("/port_trunk_cfg.json", payload)

    def set_vlans(self, payload: dict[str, Any]) -> None:
        self.post_json("/tag_vlan.json", payload)

    def delete_vlan(self, vlan_id: int) -> None:
        """Send the UI's single-VLAN deletion payload.

        Mirrors ``TagVlanDeleteSingle`` in the QSS 2.2.3 ``tag_basedvlan.js``,
        whose caller passes the ID through ``parseInt``. The physical switch
        silently ignores a string ID here, unlike ``updatedVlans``, so the ID
        must be sent as a JSON integer. The CLI's ``delete-vlan`` command adds
        the identity, membership, backup, and read-back gates.
        """
        if vlan_id == 1:
            raise ValueError("VLAN 1 cannot be deleted")
        self.post_json("/tag_vlan.json", {"updatedVlans": [], "deletedVlans": [int(vlan_id)]})

    def save(self) -> None:
        self.post_empty("/save_all_configs.json")

    def download_backup(self) -> bytes:
        try:
            response = self._get_with_retry("/config/download")
        except httpx.HTTPError as exc:
            raise ApiError("configuration backup request failed") from exc
        self._raise_for_status(response)
        if _looks_like_html(response.content):
            raise AuthenticationError("configuration backup returned HTML instead of a file")
        if _content_is_login_redirect(response.content):
            raise AuthenticationError("configuration backup was redirected to login")
        if _content_is_api_error(response.content):
            raise ApiError("configuration backup returned an API error object")
        if not response.content:
            raise ApiError("configuration backup was empty")
        return response.content

    def _get_with_retry(self, path: str) -> httpx.Response:
        """Issue a read-only GET, retrying once if the switch drops the socket.

        Only idempotent reads use this. POSTs are never retried because a
        dropped connection cannot prove whether the switch applied the write.
        """
        try:
            return self._client.get(path)
        except httpx.RemoteProtocolError:
            return self._client.get(path)

    def get_json(self, path: str) -> dict[str, Any]:
        try:
            response = self._get_with_retry(path)
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
        _raise_for_api_error(data, f"GET {path}")
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
        _raise_for_api_error(data, f"POST {path}")
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
        if not isinstance(data, dict):
            raise ApiError(f"POST {path} returned a non-object JSON value")
        if _is_login_redirect(data):
            raise AuthenticationError(f"POST {path} was redirected to login")
        _raise_for_api_error(data, f"POST {path}")
        return data

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code == 401:
            raise AuthenticationError("switch rejected the session")
        if 300 <= response.status_code < 400:
            location = response.headers.get("location", "")
            if location.endswith("/login.html") or "/login.html?" in location:
                raise AuthenticationError("switch redirected the session to login")
            raise ApiError(f"switch returned HTTP {response.status_code} redirect")
        if not 200 <= response.status_code < 300:
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
    except json.JSONDecodeError, UnicodeDecodeError:
        return False
    return isinstance(data, dict) and _is_login_redirect(data)


def _content_is_api_error(content: bytes) -> bool:
    try:
        data = json.loads(content)
    except json.JSONDecodeError, UnicodeDecodeError:
        return False
    if not isinstance(data, dict):
        return False
    try:
        _raise_for_api_error(data, "backup")
    except ApiError:
        return True
    return False


def _is_login_redirect(data: dict[str, Any]) -> bool:
    redirect = str(data.get("redirect", ""))
    return redirect.endswith("/login.html") or "/login.html?" in redirect


def _raise_for_api_error(data: dict[str, Any], operation: str) -> None:
    error = data.get("error")
    result = str(data.get("result", "")).lower()
    alert_key = data.get("alert_key")
    has_error = error is not None and error != "" and error != 0
    alert_failed = alert_key is not None and (
        not isinstance(alert_key, str) or "success" not in alert_key.lower()
    )
    if has_error or alert_failed or result in {"error", "fail", "failed", "failure"}:
        raise ApiError(f"{operation} returned an API error")


def _vlan_ids(vlans: list[dict[str, Any]]) -> frozenset[int]:
    values: list[int] = []
    try:
        for vlan in vlans:
            vlan_id = _nonnegative_integer(vlan["vlan_id"])
            if not 1 <= vlan_id <= 4094:
                raise ValueError
            values.append(vlan_id)
    except (KeyError, TypeError, ValueError) as exc:
        raise ApiError("VLAN event stream returned an invalid VLAN ID") from exc
    if len(values) != len(set(values)):
        raise ApiError("VLAN event stream returned duplicate VLAN IDs")
    return frozenset(values)


def _vlan_list_signature(data: dict[str, Any]) -> frozenset[int]:
    raw_ids = data.get("vlan_ids")
    if not isinstance(raw_ids, list):
        raise ApiError("VLAN list response must contain a vlan_ids array")
    try:
        vlan_ids = [_nonnegative_integer(value) for value in raw_ids]
    except ValueError as exc:
        raise ApiError("VLAN list response contains a non-integer VLAN ID") from exc
    if len(vlan_ids) != len(set(vlan_ids)) or any(not 1 <= value <= 4094 for value in vlan_ids):
        raise ApiError("VLAN list response contains invalid or duplicate VLAN IDs")
    return frozenset(vlan_ids)


def _pvid_signature(data: dict[str, Any]) -> tuple[int, ...]:
    raw_pvids = data.get("port_pvids")
    if not isinstance(raw_pvids, list):
        raise ApiError("PVID response must contain a port_pvids array")
    try:
        pvids = tuple(_nonnegative_integer(value) for value in raw_pvids)
    except ValueError as exc:
        raise ApiError("PVID response contains a non-integer value") from exc
    if len(pvids) != 11:
        raise ApiError(f"PVID response has {len(pvids)} entries; expected 11")
    if pvids[0] != 0:
        raise ApiError("PVID response reserved element zero must be 0")
    if any(not 1 <= value <= 4094 for value in pvids[1:]):
        raise ApiError("PVID response contains an invalid per-port PVID")
    return pvids[1:]


def _nonnegative_integer(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        return int(value)
    raise ValueError
