from __future__ import annotations

import hashlib
import json

import httpx
import pytest

from qsw_l2110.client import QswL2110Client, parse_sse_lines
from qsw_l2110.errors import ApiError, AuthenticationError


def test_client_rejects_credentials_or_paths_in_host_url() -> None:
    with pytest.raises(ValueError, match="only scheme, hostname"):
        QswL2110Client("https://admin:secret@switch/ui")


def test_parse_sse_lines_ignores_metadata() -> None:
    events = parse_sse_lines(
        [
            ": heartbeat",
            "event: message",
            'data: {"PortNum":10}',
            "",
            'data: {"vlan_id":"1","port_states":[0,1]}',
        ]
    )
    assert events == [
        {"PortNum": 10},
        {"vlan_id": "1", "port_states": [0, 1]},
    ]


def test_authentication_hashes_credentials_and_establishes_session() -> None:
    observed_query: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed_query.update(dict(request.url.params))
        return httpx.Response(
            200,
            json={"redirect": "https://switch/index.html"},
            headers={"set-cookie": "session=abc123; HttpOnly; SameSite=Lax"},
        )

    client = QswL2110Client(
        "https://switch",
        transport=httpx.MockTransport(handler),
    )
    client.authenticate("admin", "secret")
    client.close()
    assert observed_query == {
        "loginusr": hashlib.md5(b"admin", usedforsecurity=False).hexdigest(),
        "loginpwd": hashlib.md5(b"secret", usedforsecurity=False).hexdigest(),
    }


def test_authentication_requires_session_cookie() -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={}))
    client = QswL2110Client("https://switch", transport=transport)
    with pytest.raises(AuthenticationError, match="session cookie"):
        client.authenticate("admin", "wrong")
    client.close()


def test_reads_vlan_sse_stream() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/authorize":
            return httpx.Response(200, headers={"set-cookie": "session=abc"}, json={})
        if request.url.path == "/tag_vlan.json":
            body = "\n".join(
                [
                    'data: {"PortNum":10}',
                    "",
                    'data: {"vlan_id":"1","vlan_name":"default","port_states":[0,1]}',
                    "",
                ]
            )
            return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
        return httpx.Response(404)

    client = QswL2110Client("https://switch", transport=httpx.MockTransport(handler))
    client.authenticate("admin", "secret")
    assert client.get_vlans()[0]["vlan_id"] == "1"
    client.close()


def test_vlan_sse_timeout_after_data_fails_closed() -> None:
    class TimeoutStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b'data: {"PortNum":10}\n\ndata: {"vlan_id":"1"}\n\n'
            raise httpx.ReadTimeout("simulated truncated stream")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/authorize":
            return httpx.Response(200, headers={"set-cookie": "session=abc"}, json={})
        return httpx.Response(
            200,
            stream=TimeoutStream(),
            headers={"content-type": "text/event-stream"},
        )

    client = QswL2110Client("https://switch", transport=httpx.MockTransport(handler))
    client.authenticate("admin", "secret")
    with pytest.raises(ApiError, match="timed out before cleanly closing"):
        client.get_vlans()
    client.close()


def test_vlan_sse_protocol_error_is_wrapped_and_fails_closed() -> None:
    class BrokenStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b'data: {"PortNum":10}\n\n'
            raise httpx.RemoteProtocolError("simulated disconnect")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/authorize":
            return httpx.Response(200, headers={"set-cookie": "session=abc"}, json={})
        return httpx.Response(200, stream=BrokenStream())

    client = QswL2110Client("https://switch", transport=httpx.MockTransport(handler))
    client.authenticate("admin", "secret")
    with pytest.raises(ApiError, match="failed before cleanly closing"):
        client.get_vlans()
    client.close()


def test_vlan_snapshot_rejects_sse_missing_pvid_inventory_vlan() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/authorize":
            return httpx.Response(200, headers={"set-cookie": "session=abc"}, json={})
        if request.url.path == "/get_vlan_list.json":
            return httpx.Response(200, json={"vlan_ids": [1, 20]})
        if request.url.path == "/all_port_pvid.json":
            return httpx.Response(
                200,
                json={"port_pvids": [0, *([1] * 10)]},
            )
        if request.url.path == "/tag_vlan.json":
            body = "\n".join(
                [
                    'data: {"PortNum":10}',
                    "",
                    'data: {"vlan_id":"1","port_states":[0,1]}',
                    "",
                ]
            )
            return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
        return httpx.Response(404)

    client = QswL2110Client("https://switch", transport=httpx.MockTransport(handler))
    client.authenticate("admin", "secret")
    with pytest.raises(ApiError, match="changed or was incomplete"):
        client.get_vlan_snapshot()
    client.close()


def test_post_json_uses_expected_payload() -> None:
    observed: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/authorize":
            return httpx.Response(200, headers={"set-cookie": "session=abc"}, json={})
        observed.update(json.loads(request.content))
        return httpx.Response(200, json={"result": "ok"})

    client = QswL2110Client("https://switch", transport=httpx.MockTransport(handler))
    client.authenticate("admin", "secret")
    client.set_lag_config({"system_priority": "32768"})
    client.close()
    assert observed == {"system_priority": "32768"}


def test_post_json_rejects_failure_alert_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/authorize":
            return httpx.Response(200, headers={"set-cookie": "session=abc"}, json={})
        return httpx.Response(200, json={"alert_key": "alert_save_fail"})

    client = QswL2110Client("https://switch", transport=httpx.MockTransport(handler))
    client.authenticate("admin", "secret")
    with pytest.raises(ApiError, match="returned an API error"):
        client.save()
    client.close()


def test_save_rejects_non_object_json_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/authorize":
            return httpx.Response(200, headers={"set-cookie": "session=abc"}, json={})
        return httpx.Response(200, json=["unexpected"])

    client = QswL2110Client("https://switch", transport=httpx.MockTransport(handler))
    client.authenticate("admin", "secret")
    with pytest.raises(ApiError, match="non-object JSON"):
        client.save()
    client.close()


def test_backup_rejects_json_login_redirect() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/authorize":
            return httpx.Response(200, headers={"set-cookie": "session=abc"}, json={})
        return httpx.Response(200, json={"redirect": "https://switch/login.html"})

    client = QswL2110Client("https://switch", transport=httpx.MockTransport(handler))
    client.authenticate("admin", "secret")
    with pytest.raises(AuthenticationError, match="redirected to login"):
        client.download_backup()
    client.close()


def test_backup_rejects_structured_api_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/authorize":
            return httpx.Response(200, headers={"set-cookie": "session=abc"}, json={})
        return httpx.Response(200, json={"error": {"code": 1}})

    client = QswL2110Client("https://switch", transport=httpx.MockTransport(handler))
    client.authenticate("admin", "secret")
    with pytest.raises(ApiError, match="API error object"):
        client.download_backup()
    client.close()


def test_redirect_status_is_not_treated_as_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/authorize":
            return httpx.Response(200, headers={"set-cookie": "session=abc"}, json={})
        return httpx.Response(302, headers={"location": "/elsewhere"})

    client = QswL2110Client("https://switch", transport=httpx.MockTransport(handler))
    client.authenticate("admin", "secret")
    with pytest.raises(ApiError, match="HTTP 302 redirect"):
        client.get_lag_config()
    client.close()


def test_requests_ask_for_fresh_connections() -> None:
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("connection"))
        if request.url.path == "/authorize":
            return httpx.Response(200, headers={"set-cookie": "session=abc"}, json={})
        return httpx.Response(200, json={"model": "QSW-L2110-10T"})

    client = QswL2110Client("https://switch", transport=httpx.MockTransport(handler))
    client.authenticate("admin", "secret")
    client.get_json("/get_model_name.json")
    client.close()
    assert seen == ["close", "close"]


def test_read_only_get_retries_once_after_dropped_socket() -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/authorize":
            return httpx.Response(200, headers={"set-cookie": "session=abc"}, json={})
        calls["count"] += 1
        if calls["count"] == 1:
            raise httpx.RemoteProtocolError("server closed idle connection")
        return httpx.Response(200, json={"model": "QSW-L2110-10T"})

    client = QswL2110Client("https://switch", transport=httpx.MockTransport(handler))
    client.authenticate("admin", "secret")
    assert client.get_json("/get_model_name.json") == {"model": "QSW-L2110-10T"}
    assert calls["count"] == 2
    client.close()


def test_read_only_get_gives_up_after_second_dropped_socket() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/authorize":
            return httpx.Response(200, headers={"set-cookie": "session=abc"}, json={})
        raise httpx.RemoteProtocolError("server closed idle connection")

    client = QswL2110Client("https://switch", transport=httpx.MockTransport(handler))
    client.authenticate("admin", "secret")
    with pytest.raises(ApiError, match="GET /get_model_name.json failed"):
        client.get_json("/get_model_name.json")
    client.close()


def test_post_is_never_retried_after_dropped_socket() -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/authorize":
            return httpx.Response(200, headers={"set-cookie": "session=abc"}, json={})
        calls["count"] += 1
        raise httpx.RemoteProtocolError("server closed idle connection")

    client = QswL2110Client("https://switch", transport=httpx.MockTransport(handler))
    client.authenticate("admin", "secret")
    with pytest.raises(ApiError, match="POST /port_trunk_cfg.json failed"):
        client.post_json("/port_trunk_cfg.json", {})
    assert calls["count"] == 1
    client.close()
