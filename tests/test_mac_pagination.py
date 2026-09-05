import httpx
import pytest

from qsw_l2110.client import QswL2110Client
from qsw_l2110.errors import ApiError


def test_mac_table_reads_all_pages_and_preserves_order():
    offsets = []

    def handler(request):
        offset = int(request.url.params.get("offset", "0"))
        offsets.append(offset)
        return httpx.Response(
            200,
            json={
                "batch": [{"mac": f"02:00:00:00:00:{offset:02x}"}],
                "has_more": offset < 2,
                "next_offset": offset + 1,
                "count": 1,
            },
        )

    with QswL2110Client("https://switch", transport=httpx.MockTransport(handler)) as client:
        result = client.get_mac_table()
    assert offsets == [0, 1, 2]
    assert result["count"] == 3
    assert [row["mac"] for row in result["batch"]] == [f"02:00:00:00:00:{i:02x}" for i in range(3)]
    assert result["has_more"] is False


def test_mac_table_preserves_legacy_response():
    legacy = {"batch": [{"mac": "02:00:00:00:00:01"}]}
    with QswL2110Client(
        "https://switch", transport=httpx.MockTransport(lambda _: httpx.Response(200, json=legacy))
    ) as client:
        assert client.get_mac_table() == legacy


@pytest.mark.parametrize(
    "page",
    [
        {"batch": [{}], "has_more": True, "next_offset": 0},
        {"batch": [], "has_more": True, "next_offset": 8},
        {"batch": [{}], "has_more": True, "next_offset": True},
        {"batch": [{}], "has_more": "false"},
        {"batch": "bad", "has_more": False},
    ],
)
def test_mac_table_rejects_broken_pagination(page):
    with (
        QswL2110Client(
            "https://switch",
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=page)),
        ) as client,
        pytest.raises(ApiError),
    ):
        client.get_mac_table()


def test_mac_table_does_not_return_partial_data_after_later_error():
    def handler(request):
        if "offset" in request.url.params:
            return httpx.Response(503)
        return httpx.Response(200, json={"batch": [{}], "has_more": True, "next_offset": 8})

    with QswL2110Client("https://switch", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ApiError, match="503"):
            client.get_mac_table()
