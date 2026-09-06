import json
import struct
from contextlib import contextmanager, nullcontext
from types import SimpleNamespace

import pytest

from tools import mirror_reboot as runner


@pytest.fixture
def receiver(tmp_path, monkeypatch):
    monkeypatch.setattr(runner.time, "time", lambda: 1000)
    row = {"pid": 123, "capture_pid": 124, "interface": "en9", "epoch": 1000, "expires_epoch": 2200}
    (tmp_path / "ready.json").write_text(json.dumps(row))
    (tmp_path / "en9.pcap").write_bytes(struct.pack("<IHHIIII", 0xA1B2C3D4, 2, 4, 0, 0, 256, 1))

    def process(args, **_kwargs):
        name = "mirror_receiver_macos.py" if args[2] == "123" else "/usr/sbin/tcpdump"
        return SimpleNamespace(returncode=0, stdout=name + " --output " + str(tmp_path))

    monkeypatch.setattr(runner.subprocess, "run", process)
    return tmp_path, row


def test_live_receiver_with_valid_pcap_is_required(receiver):
    directory, row = receiver
    assert runner.receiver_ready(directory, 900) == row
    (directory / "stopped.json").touch()
    with pytest.raises(RuntimeError, match="stopped"):
        runner.receiver_ready(directory)


@pytest.mark.parametrize(
    "field,value", [("epoch", 990), ("expires_epoch", 1800), ("interface", "en8")]
)
def test_stale_expiring_or_wrong_receiver_cannot_start_reboot(receiver, field, value):
    directory, row = receiver
    row[field] = value
    (directory / "ready.json").write_text(json.dumps(row))
    with pytest.raises(RuntimeError):
        runner.receiver_ready(directory, 900)


def test_reused_process_id_is_not_readiness(receiver, monkeypatch):
    directory, _ = receiver
    monkeypatch.setattr(
        runner.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="unrelated")
    )
    with pytest.raises(RuntimeError, match="process is missing"):
        runner.receiver_ready(directory)


def test_uptime_parser_requires_the_observed_format():
    assert runner.uptime_seconds("0 Days 6 Hours 38 Minutes") == 23880
    assert runner.uptime_seconds("0 Days 0 Hours 0 Minutes") == 0
    with pytest.raises(RuntimeError, match="Unknown"):
        runner.uptime_seconds("unknown")


@pytest.mark.parametrize("control_passes,ambiguous_reboot", [(False, False), (True, True)])
def test_control_gates_reboot_and_ambiguous_reboot_is_never_retried(
    tmp_path, monkeypatch, control_passes, ambiguous_reboot
):
    directory, capture = tmp_path / "run", tmp_path / "receiver"
    directory.mkdir()
    capture.mkdir()
    (capture / "stopped.json").touch()
    (capture / "receiver-restored.json").write_text('{"ok": true}')
    plan = {
        "configuration": {},
        "switch_mac": "02:00:00:00:00:01",
        "receiver_mac": "02:00:00:00:00:03",
        "router": {"mapping": {"eth2": 3, "eth3": 4}},
    }
    (directory / "plan.json").write_text(json.dumps(plan))
    (directory / "readiness.json").write_text(
        json.dumps(
            {
                "mac_table": {
                    "batch": [
                        {"mac_addr": "02:00:00:00:00:03", "portid": 2},
                    ]
                }
            }
        )
    )
    events, clock = [], [1000]
    monkeypatch.setattr(
        runner,
        "time",
        SimpleNamespace(
            time=lambda: clock[0],
            monotonic=lambda: clock[0],
            sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
        ),
    )
    monkeypatch.setattr(runner, "exclusive", nullcontext)
    monkeypatch.setattr(runner, "receiver_ready", lambda *a: {})
    monkeypatch.setattr(runner, "configuration", lambda snapshot: {})
    monkeypatch.setattr(runner, "matching_packets", lambda *a: [{}, {}] if control_passes else [])

    class Device:
        rebooted = False

        def __init__(self, *_args):
            pass

        def session(self):
            return nullcontext(self)

        def snapshot(self, label):
            return {
                "identity": {},
                "lags": {"Port_2": {"portTypeId_2": "0"}},
                "ports": {"Port_2": {"Spd_Duplex_Actual": "1000MbpsFull"}},
            }

        def identity(self, *_args):
            pass

        def management_path(self, *_args):
            pass

        def get_identity(self):
            return {}

        def get_json(self, path):
            assert path == "/port_mirror.json"
            return {
                "PortNum": "10",
                "MonitoringPortId": "7",
                **{
                    f"Port_{p}": {"Ingress_Status": "Disabled", "Egress_Status": "Disabled"}
                    for p in range(1, 11)
                },
            }

        def get_system_status(self):
            return {
                "uptime": "0 Days 0 Hours 0 Minutes"
                if self.rebooted
                else "0 Days 6 Hours 0 Minutes"
            }

        def post_empty(self, path):
            assert path == "/system_reboot.json"
            events.append("reboot")
            self.rebooted = True
            if ambiguous_reboot:
                raise RuntimeError("response lost after accepted reboot")

    class Router:
        def __init__(self, *_args):
            pass

        def preflight(self):
            return {"native": {"members": {"eth2": {"actor_port": 1}, "eth3": {"actor_port": 2}}}}

        def native(self):
            return clock[0], "System MAC address: 02:00:00:00:00:02\n"

        def start(self, **_kwargs):
            events.append("recorder-start")
            (directory / "recorder.json").write_text("{}")

        def command(self, command):
            events.append(command)

        def stop(self):
            events.append("recorder-stop")

        def fetch(self, label):
            events.append("recorder-fetch")

    @contextmanager
    def mirror(_client, sources, destination, **_kwargs):
        assert destination == 2
        events.append("mirror-" + str(sources[0]))
        try:
            yield
        finally:
            events.append("mirror-off")

    def guard(*_args):
        events.append("guard-armed")
        return {"remote": "/owned-guard", "unit": "owned-unit"}

    monkeypatch.setattr(runner, "Device", Device)
    monkeypatch.setattr(runner, "RouterRecorder", Router)
    monkeypatch.setattr(runner, "stage_guard", guard)
    monkeypatch.setattr(runner, "retire_guard", lambda *_args: {"credentials_removed": True})
    monkeypatch.setattr(runner, "ingress_mirror", mirror)
    code = runner.run(directory, capture)
    result = json.loads((directory / "result.json").read_text())
    assert events.index("guard-armed") < events.index("mirror-3")
    assert result["configuration_restored"]
    assert "recorder-stop" in events and "recorder-fetch" in events
    if control_passes:
        assert code == 0 and events.count("reboot") == 1
        assert len(result["windows"]) == 4
        assert result["windows"][2]["end"] - result["windows"][2]["start"] == 450
    else:
        assert code == 1 and "reboot" not in events
        assert "mirror-4" not in events
