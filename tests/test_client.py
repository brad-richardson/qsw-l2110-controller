from __future__ import annotations

import hashlib
import json

import httpx
import pytest

from qsw_l2110.client import QswL2110Client, parse_sse_lines
from qsw_l2110.errors import AuthenticationError


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
