"""Local, independent emulator for the firmware-derived QSS HTTP contract.

This is a development aid, not a model of the switch data plane. It intentionally
uses only the Python standard library and does not import controller code.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import threading
from copy import deepcopy
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit


def _factory_lags(port_count: int) -> dict[str, Any]:
    result: dict[str, Any] = {"PortNum": port_count, "system_priority": "32768"}
    for port in range(1, port_count + 1):
        result[f"Port_{port}"] = {
            f"portTypeId_{port}": "0",
            f"portPriorityId_{port}": "128",
            f"lacpTimeoutId_{port}": "0",
            f"Port_{port}_grpInd": "1",
            f"Port_{port}_state": 0,
        }
    return result


def _factory_vlans(port_count: int) -> list[dict[str, Any]]:
    return [
        {
            "vlan_id": "1",
            "vlan_name": "default",
            "port_states": [0, *([1] * port_count)],
        }
    ]


@dataclass(slots=True)
class QssEmulatorState:
    """Mutable state and fault controls exposed to integration tests."""

    model: str = "QSW-L2110-10T"
    firmware: str = "2.2.3.20260713"
    username: str = "admin"
    password: str = "emulator-only"
    port_count: int = 10
    converge_lag_writes: bool = True
    converge_vlan_writes: bool = True
    update_pvids_on_vlan_write: bool = True
    fail_save: bool = False
    lags: dict[str, Any] = field(default_factory=lambda: _factory_lags(10))
    vlans: list[dict[str, Any]] = field(default_factory=lambda: _factory_vlans(10))
    pvids: list[int] = field(default_factory=lambda: [0, *([1] * 10)])
    requests: list[tuple[str, str]] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    save_count: int = 0
    persisted_lags: dict[str, Any] | None = None
    persisted_vlans: list[dict[str, Any]] | None = None
    persisted_pvids: list[int] | None = None
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self) -> None:
        if self.port_count != 10:
            self.lags = _factory_lags(self.port_count)
            self.vlans = _factory_vlans(self.port_count)
            self.pvids = [0, *([1] * self.port_count)]

    def record(self, method: str, path: str) -> None:
        # Record the path without the query string: authorization query values
        # are replayable credential digests and must never enter test logs.
        with self.lock:
            self.requests.append((method, path))

    def backup(self) -> bytes:
        with self.lock:
            self.actions.append("backup")
            snapshot = {"lags": self.lags, "pvids": self.pvids, "vlans": self.vlans}
            return b"QSS-EMULATOR\x00" + json.dumps(snapshot, sort_keys=True).encode()

    def apply_lags(self, payload: dict[str, Any]) -> None:
        required = {"system_priority"}
        for port in range(1, self.port_count + 1):
            required.update(
                {
                    f"portTypeId_{port}",
                    f"portPriorityId_{port}",
                    f"lacpTimeoutId_{port}",
                    f"Port_{port}_grpInd",
                }
            )
        missing = sorted(required - payload.keys())
        if missing:
            raise ValueError(f"incomplete LAG object; missing {', '.join(missing)}")

        with self.lock:
            self.actions.append("set-lags")
            if not self.converge_lag_writes:
                return
            self.lags["system_priority"] = str(payload["system_priority"])
            for port in range(1, self.port_count + 1):
                target = self.lags[f"Port_{port}"]
                for field_name in (
                    f"portTypeId_{port}",
                    f"portPriorityId_{port}",
                    f"lacpTimeoutId_{port}",
                    f"Port_{port}_grpInd",
                ):
                    target[field_name] = str(payload[field_name])

    def apply_vlans(self, payload: dict[str, Any]) -> None:
        updated = payload.get("updatedVlans")
        deleted = payload.get("deletedVlans")
        if not isinstance(updated, list) or not isinstance(deleted, list):
            raise ValueError("expected updatedVlans and deletedVlans lists")
        if updated and deleted:
            raise ValueError("the UI never mixes updates and deletions in one request")
        if any(not isinstance(item, int) or isinstance(item, bool) for item in deleted):
            # Observed on hardware 2026-09-04: string IDs are silently ignored.
            deleted_ids: list[str] = []
        else:
            deleted_ids = [str(item) for item in deleted]
        if "1" in deleted_ids:
            raise ValueError("VLAN 1 cannot be deleted")

        normalized: list[dict[str, Any]] = []
        for item in updated:
            if not isinstance(item, dict):
                raise ValueError("updatedVlans entries must be objects")
            states = item.get("port_states")
            if not isinstance(states, list) or len(states) != self.port_count + 1:
                raise ValueError("VLAN port_states must include the reserved element zero")
            if states[0] != 0 or any(state not in {0, 1, 2} for state in states):
                raise ValueError("VLAN port_states contains an invalid state")
            normalized.append(
                {
                    "vlan_id": str(item["vlan_id"]),
                    "vlan_name": str(item.get("vlan_name", "")),
                    "port_states": list(states),
                }
            )

        with self.lock:
            if deleted_ids:
                self.actions.append("delete-vlans")
                if not self.converge_vlan_writes:
                    return
                known = {str(item["vlan_id"]) for item in self.vlans}
                missing = [vlan_id for vlan_id in deleted_ids if vlan_id not in known]
                if missing:
                    raise ValueError(f"unknown VLAN {', '.join(missing)}")
                self.vlans = [
                    item for item in self.vlans if str(item["vlan_id"]) not in deleted_ids
                ]
                return
            self.actions.append("set-vlans")
            if not self.converge_vlan_writes:
                return
            positions = {item["vlan_id"]: index for index, item in enumerate(normalized)}
            for item in normalized:
                destination = item["vlan_id"]
                for port, port_state in enumerate(item["port_states"][1:], start=1):
                    source = str(self.pvids[port])
                    if (
                        port_state == 1
                        and source != destination
                        and source in positions
                        and positions[destination] > positions[source]
                    ):
                        raise ValueError(
                            f"destination VLAN {destination} must precede source VLAN {source}"
                        )
            by_id = {str(item["vlan_id"]): deepcopy(item) for item in self.vlans}
            for item in normalized:
                by_id[item["vlan_id"]] = item
            self.vlans = sorted(by_id.values(), key=lambda item: int(item["vlan_id"]))
            if self.update_pvids_on_vlan_write:
                for port in range(1, self.port_count + 1):
                    owners = [
                        int(item["vlan_id"])
                        for item in self.vlans
                        if item["port_states"][port] == 1
                    ]
                    if len(owners) == 1:
                        self.pvids[port] = owners[0]

    def save(self) -> None:
        with self.lock:
            self.actions.append("save")
            if self.fail_save:
                raise ValueError("simulated save failure")
            self.save_count += 1
            self.persisted_lags = deepcopy(self.lags)
            self.persisted_vlans = deepcopy(self.vlans)
            self.persisted_pvids = list(self.pvids)


class _Server(ThreadingHTTPServer):
    daemon_threads = True


class QssEmulator:
    """A loopback-only QSS contract server usable as a context manager."""

    def __init__(self, *, port: int = 0, state: QssEmulatorState | None = None) -> None:
        self.state = state or QssEmulatorState()
        self._server = _Server(("127.0.0.1", port), _handler(self.state))
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="qss-contract-emulator",
            daemon=True,
        )

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        if self._thread.is_alive():
            self._server.shutdown()
            self._thread.join(timeout=5)
        self._server.server_close()

    def __enter__(self) -> QssEmulator:
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _handler(state: QssEmulatorState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, _format: str, *_args: object) -> None:
            # BaseHTTPRequestHandler logs the full authorize URL by default.
            # Silence it so the replayable digest query never reaches a log.
            return

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            parsed = urlsplit(self.path)
            path = parsed.path
            state.record("GET", path)
            if path == "/authorize":
                self._authorize(parse_qs(parsed.query))
                return
            if not self._authenticated():
                self._json(401, {"error": "not authenticated"})
                return
            if path == "/get_model_name.json":
                self._json(200, {"model_name": state.model})
            elif path == "/status.json":
                self._json(
                    200,
                    {
                        "des": "QSS contract emulator",
                        "fw_ver": state.firmware,
                        "hw_ver": "emulated",
                    },
                )
            elif path == "/port_trunk_cfg.json":
                with state.lock:
                    self._json(200, deepcopy(state.lags))
            elif path == "/port_trunk_refresh.json":
                self._json(200, self._lag_status())
            elif path == "/tag_vlan.json":
                self._vlan_events()
            elif path == "/get_vlan_list.json":
                self._json(200, self._vlan_ids())
            elif path == "/all_port_pvid.json":
                self._json(200, self._pvids())
            elif path == "/config/download":
                self._bytes(200, state.backup(), "application/octet-stream")
            else:
                self._json(404, {"error": "unknown endpoint"})

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            path = urlsplit(self.path).path
            state.record("POST", path)
            if not self._authenticated():
                self._json(401, {"error": "not authenticated"})
                return
            try:
                if path == "/port_trunk_cfg.json":
                    state.apply_lags(self._request_json())
                elif path == "/tag_vlan.json":
                    state.apply_vlans(self._request_json())
                elif path == "/save_all_configs.json":
                    state.save()
                else:
                    self._json(404, {"error": "unknown endpoint"})
                    return
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                self._json(400, {"error": str(exc)})
                return
            self._json(200, {"result": "ok"})

        def _authorize(self, query: dict[str, list[str]]) -> None:
            expected_user = hashlib.md5(state.username.encode(), usedforsecurity=False).hexdigest()
            expected_password = hashlib.md5(
                state.password.encode(), usedforsecurity=False
            ).hexdigest()
            supplied_user = query.get("loginusr", [""])[0]
            supplied_password = query.get("loginpwd", [""])[0]
            if not (
                hmac.compare_digest(supplied_user, expected_user)
                and hmac.compare_digest(supplied_password, expected_password)
            ):
                self._json(200, {"redirect": "/login.html"})
                return
            self._json(
                200,
                {"redirect": "/index.html"},
                headers=[
                    ("Set-Cookie", "session=qss-emulator; HttpOnly; SameSite=Lax"),
                    ("Set-Cookie", f"user={state.username}; SameSite=Lax"),
                ],
            )

        def _authenticated(self) -> bool:
            cookie = SimpleCookie()
            cookie.load(self.headers.get("Cookie", ""))
            session = cookie.get("session")
            return session is not None and hmac.compare_digest(session.value, "qss-emulator")

        def _request_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            value = json.loads(self.rfile.read(length))
            if not isinstance(value, dict):
                raise ValueError("request body must be a JSON object")
            return value

        def _lag_status(self) -> dict[str, Any]:
            result: dict[str, Any] = {"PortNum": state.port_count}
            with state.lock:
                for port in range(1, state.port_count + 1):
                    configured = state.lags[f"Port_{port}"][f"portTypeId_{port}"] != "0"
                    result[f"Port_{port}"] = {f"Port_{port}_state": int(configured)}
            return result

        def _pvids(self) -> dict[str, Any]:
            with state.lock:
                return {"port_pvids": list(state.pvids)}

        def _vlan_ids(self) -> dict[str, Any]:
            with state.lock:
                return {"vlan_ids": [str(vlan["vlan_id"]) for vlan in state.vlans]}

        def _vlan_events(self) -> None:
            with state.lock:
                entries = [{"PortNum": state.port_count}, *deepcopy(state.vlans)]
            body = b"".join(
                b"data: " + json.dumps(entry, separators=(",", ":")).encode() + b"\n\n"
                for entry in entries
            )
            self._bytes(200, body, "text/event-stream")

        def _json(
            self,
            status: int,
            value: dict[str, Any],
            *,
            headers: list[tuple[str, str]] | None = None,
        ) -> None:
            body = json.dumps(value, separators=(",", ":")).encode()
            self._bytes(status, body, "application/json", headers=headers)

        def _bytes(
            self,
            status: int,
            body: bytes,
            content_type: str,
            *,
            headers: list[tuple[str, str]] | None = None,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for name, value in headers or []:
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a loopback QSS contract emulator")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    emulator = QssEmulator(port=args.port)
    emulator.start()
    print(f"QSS contract emulator: {emulator.base_url}")
    print("Credentials: admin / emulator-only")
    print("Press Ctrl-C to stop.")
    try:
        emulator._thread.join()
    except KeyboardInterrupt:
        pass
    finally:
        emulator.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
