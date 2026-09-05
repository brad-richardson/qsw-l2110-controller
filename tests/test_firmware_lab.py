from __future__ import annotations

import hashlib
import socket
import stat
from copy import deepcopy
from pathlib import Path

import pytest

from qsw_l2110.errors import ApiError
from tools import firmware_lab as lab


@pytest.fixture
def prepared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    inventory = {}
    for version in lab.SEQUENCE:
        data = (version.encode() * 3000)[: lab.CHUNK_SIZE * 2 + 9]
        name = version + ".img"
        (tmp_path / name).write_bytes(data)
        inventory[version] = {
            "firmware": version,
            "filename": name,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    monkeypatch.setattr(lab, "catalog", lambda: inventory)
    lags = {"system_priority": 32768}
    ports = {}
    for port in range(1, 11):
        lags[f"Port_{port}"] = {
            f"portTypeId_{port}": 2 if port < 5 else 0,
            f"portPriorityId_{port}": 128,
            f"lacpTimeoutId_{port}": 0,
            f"Port_{port}_grpInd": 1 if port < 3 else 4,
        }
        ports[f"Port_{port}"] = {
            "Port_Status": "Enabled",
            "Spd_Duplex_Cfg": "Auto",
            "Flow_Ctrl_Cfg": "On",
            "EEE_Status": "eee_inactive",
        }
    snapshot = {
        "identity": {"model": {"model_name": "QSW-L2110-10T"}, "status": {"fw_ver": lab.BASELINE}},
        "lags": lags,
        "ports": ports,
        "vlans": [{"vlan_id": 1, "vlan_name": "default", "port_states": [0] + [1] * 10}],
        "pvids": {"port_pvids": [0] + [1] * 10},
    }
    plan = lab.prepare(snapshot, tmp_path)
    return tmp_path, snapshot, plan


def test_prepare_preserves_source_and_production_and_requires_long_baseline(prepared):
    directory, snapshot, plan = prepared
    original = deepcopy(snapshot)
    plan = lab.prepare(snapshot, directory)
    assert snapshot == original
    assert len(plan["readiness_items"]) == 2
    assert plan["configuration"]["lags"]["lacpTimeoutId_1"] == "1"
    assert plan["configuration"]["lags"]["lacpTimeoutId_3"] == "0"
    assert plan["configuration"]["lags"]["lacpTimeoutId_4"] == "0"
    assert plan["hardware_execution_enabled"] is False


def test_all_images_validate_before_any_simulated_upload(prepared):
    directory, _, plan = prepared
    restore = plan["stages"][-1]["image"]
    (directory / restore["filename"]).write_bytes(b"corrupt restore image")
    receiver = lab.MemorySwitch(plan)
    result = lab.rehearse(plan, directory, receiver)
    assert result["status"] == "stopped"
    assert receiver.uploads == 0
    assert result["events"] == ["capture-finalize-and-mac-cleanup"]


def test_rehearsal_uses_exact_image_bytes_without_network_and_restores_baseline(
    prepared,
    monkeypatch: pytest.MonkeyPatch,
):
    directory, _, plan = prepared

    def forbidden(*args, **kwargs):
        raise AssertionError("Rehearsal attempted network access")

    monkeypatch.setattr(socket, "socket", forbidden)
    receiver = lab.MemorySwitch(plan)
    result = lab.rehearse(plan, directory, receiver)
    assert result["status"] == "rehearsal-complete"
    assert [stage["firmware"] for stage in result["stages"]] == lab.SEQUENCE
    assert result["returned_to_original_firmware"] is True
    assert result["hardware_requests"] == 0
    assert result["chunk_calls"] == 9
    assert (
        bytes(receiver.received)
        == (directory / plan["stages"][-1]["image"]["filename"]).read_bytes()
    )


@pytest.mark.parametrize(
    "fault",
    ["ambiguous-chunk", "rejected-image", "boot-timeout", "wrong-build", "configuration-reset"],
)
def test_uncertain_or_unsafe_transition_never_advances_or_blindly_rolls_back(prepared, fault):
    directory, _, plan = prepared
    receiver = lab.MemorySwitch(plan, fault)
    result = lab.rehearse(plan, directory, receiver)
    assert result["status"] == "stopped"
    assert result["stages"] == []
    assert receiver.uploads == 1
    assert result["blind_retry_or_rollback_attempted"] is False
    assert result["events"][-1] == "capture-finalize-and-mac-cleanup"
    if fault == "ambiguous-chunk":
        assert receiver.chunk_calls == 2
        assert len(receiver.received) == lab.CHUNK_SIZE * 2


@pytest.mark.parametrize("field,value", [("model", "other-switch"), ("firmware", "2.2.1.20260417")])
def test_wrong_live_identity_is_rejected_before_preparation(prepared, field, value):
    directory, snapshot, _ = prepared
    if field == "model":
        snapshot["identity"]["model"]["model_name"] = value
    else:
        snapshot["identity"]["status"]["fw_ver"] = value
    with pytest.raises(lab.LabError):
        lab.prepare(snapshot, directory)


def test_no_hardware_execution_command_is_exposed():
    with pytest.raises(SystemExit) as error:
        lab.main(["execute"])
    assert error.value.code == 2


def test_dotenv_values_are_literal_and_environment_overrides(tmp_path, monkeypatch):
    for key in ["QSW_HOST", "QSW_USER", "QSW_PASSWORD"]:
        monkeypatch.delenv(key, raising=False)
    path = tmp_path / ".env"
    path.write_text("QSW_HOST=https://switch\nQSW_USER=admin\nQSW_PASSWORD='$(no-execution)#x'\n")
    assert lab.credentials(path)["QSW_PASSWORD"] == "$(no-execution)#x"
    monkeypatch.setenv("QSW_USER", "override")
    assert lab.credentials(path)["QSW_USER"] == "override"


def test_live_snapshot_retries_only_reads_and_protects_backup(prepared, monkeypatch):
    directory, snapshot, _ = prepared
    calls = []

    class ReadsOnlyClient:
        def __init__(self, *args, **kwargs):
            calls.append("new-session")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def authenticate(self, *args):
            calls.append("authenticate")

        def get_identity(self):
            if calls.count("new-session") == 1:
                raise ApiError("Invalid read-only JSON")
            return snapshot["identity"]

        def get_lag_config(self):
            return snapshot["lags"]

        def get_port_settings(self):
            return snapshot["ports"]

        def get_port_link_summary(self):
            return {}

        def get_lag_status(self):
            return {}

        def get_vlan_snapshot(self):
            return snapshot["vlans"], snapshot["pvids"]

        def download_backup(self):
            calls.append("backup-read")
            return b"private backup"

    monkeypatch.setattr(lab, "QswL2110Client", ReadsOnlyClient)
    monkeypatch.setattr(lab.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        lab,
        "credentials",
        lambda _: {"QSW_HOST": "https://switch", "QSW_USER": "admin", "QSW_PASSWORD": "test-only"},
    )
    output = directory / "snapshot.json"
    lab.live_snapshot(directory / ".env", output, False, directory / "qss.lock")
    assert calls == ["new-session", "authenticate", "new-session", "authenticate", "backup-read"]
    assert output.with_suffix(".cfg").read_bytes() == b"private backup"
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert stat.S_IMODE(output.with_suffix(".cfg").stat().st_mode) == 0o600
    with pytest.raises(lab.LabError, match="must be new"):
        lab.live_snapshot(directory / ".env", output, False, directory / "qss.lock")
    assert calls.count("new-session") == 2
