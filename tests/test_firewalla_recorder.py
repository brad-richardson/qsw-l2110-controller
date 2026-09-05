import json
import shlex
import subprocess
from datetime import UTC, datetime

import pytest

from tools import arp_ingress_probe, firewalla_recorder
from tools.firewalla_recorder import (
    HEREDOC,
    bootstrap_script,
    recorder_arguments,
    run_id,
    ssh_base,
    validate_names,
)


def test_run_id_and_label_validation():
    stamp = datetime(2026, 9, 5, 16, 0, tzinfo=UTC)
    assert run_id("lan-order", stamp) == "lan-order-20260905T160000Z"
    for label in ["", "-x", "a b", "$(id)", "x" * 41]:
        with pytest.raises(ValueError):
            run_id(label, stamp)


@pytest.mark.parametrize("name", ["eth0;id", "$(id)", "../x", "-i", "a" * 16, "bond 0"])
def test_names_reject_injection(name):
    with pytest.raises(ValueError):
        validate_names([name], "interface")


def test_recorder_arguments_are_bounded_and_ordered():
    arguments = recorder_arguments(
        "/home/pi/lag-recorder/run-1",
        duration=120,
        interval=2,
        snaplen=256,
        promiscuous=False,
        capture_filter="ether proto 0x8809",
        bonds=["bond0", "bond1", "bond0"],
        interfaces=["eth2", "eth3"],
    )
    assert arguments == [
        "/home/pi/lag-recorder/run-1",
        "120",
        "2",
        "256",
        "-p",
        "ether proto 0x8809",
        "bond0 bond1",
        "eth2 eth3",
    ]
    common = dict(
        interval=1,
        snaplen=256,
        promiscuous=True,
        capture_filter="",
        bonds=["bond0"],
        interfaces=["eth2"],
    )
    assert recorder_arguments("/tmp/r", duration=30, **common)[4] == ""
    for run_dir in ["relative/dir", "/tmp/../etc", "/tmp/x;rm"]:
        with pytest.raises(ValueError):
            recorder_arguments(run_dir, duration=60, **common)
    for duration in [29, 14401]:
        with pytest.raises(ValueError):
            recorder_arguments("/tmp/r", duration=duration, **common)
    with pytest.raises(ValueError):
        recorder_arguments(
            "/tmp/r",
            duration=60,
            interval=1,
            snaplen=256,
            promiscuous=False,
            capture_filter="arp; rm -rf /",
            bonds=["bond0"],
            interfaces=["eth2"],
        )


def test_bootstrap_script_installs_and_starts_detached():
    arguments = recorder_arguments(
        "/home/pi/lag-recorder/run-1",
        duration=60,
        interval=1,
        snaplen=256,
        promiscuous=False,
        capture_filter="ether proto 0x8809",
        bonds=["bond0"],
        interfaces=["eth2", "eth3"],
    )
    script = bootstrap_script("/home/pi/lag-recorder/run-1", "lag-recorder-run-1", arguments)
    assert script.startswith("set -eu\numask 077\n")
    assert f"<<'{HEREDOC}'" in script and script.count(HEREDOC) == 2
    assert "systemd-run --quiet --collect --unit=lag-recorder-run-1" in script
    assert "nohup setsid" in script
    started = [line for line in script.splitlines() if line.startswith("  systemd-run")][0]
    tail = shlex.split(started)[shlex.split(started).index("/bin/bash") :]
    assert tail == ["/bin/bash", "/home/pi/lag-recorder/run-1/recorder.sh", *arguments]
    body = script.split(f"<<'{HEREDOC}'\n", 1)[1].split(f"\n{HEREDOC}", 1)[0]
    for needle in ["/proc/net/bonding/$b", "ip -s -s link show", "journalctl -k -f", "tcpdump"]:
        assert needle in body
    with pytest.raises(ValueError):
        bootstrap_script("/tmp/r", "bad unit", arguments)


def test_ssh_base_pins_batch_mode_and_known_hosts():
    args = ssh_base(__import__("pathlib").Path("/k"), "pi@192.168.1.1")
    assert "BatchMode=yes" in args and "StrictHostKeyChecking=yes" in args
    assert args[-1] == "pi@192.168.1.1"
    for target in ["-oProxyCommand=x", "pi@host name", ""]:
        with pytest.raises(ValueError):
            ssh_base(__import__("pathlib").Path("/k"), target)


def test_start_writes_private_run_record(tmp_path, monkeypatch):
    seen = {}

    def run(args, **kwargs):
        seen["args"] = args
        seen["stdin"] = kwargs.get("input", b"").decode()
        return subprocess.CompletedProcess(args, 0, b"started=systemd-run\n", b"")

    monkeypatch.setattr(subprocess, "run", run)
    output = tmp_path / "run"
    status = firewalla_recorder.main(
        [
            "start",
            "--ssh-target",
            "pi@router",
            "--identity-file",
            "/k",
            "--interface",
            "eth2",
            "--bond",
            "bond0",
            "--duration",
            "60",
            "--label",
            "t",
            "--output",
            str(output),
        ]
    )
    assert status == 0
    assert seen["args"][-1] == "sudo -n bash -s"
    assert "recorder.sh" in seen["stdin"]
    record = json.loads((output / "run.json").read_text())
    assert record["unit"].startswith("lag-recorder-t-")
    assert record["remote_run_dir"].startswith("/home/pi/lag-recorder/t-")
    assert record["interfaces"] == ["eth2"] and record["bonds"] == ["bond0"]
    assert (output / "run.json").stat().st_mode & 0o777 == 0o600


def test_arp_probe_script_uses_probe_macs_and_rejects_bad_input():
    script = arp_ingress_probe.remote_script(
        "/tmp/arp-probe-x",
        probes=[("eth2", "02:00:00:00:00:42"), ("eth3", "02:00:00:00:00:43")],
        capture_interfaces=["eth2", "eth3"],
        target_ip="192.168.1.72",
        count=3,
        settle=3,
    )
    assert "tell 0.0.0.0" not in script  # sender address is built in the probe, not the shell
    assert "inet_aton('0.0.0.0')" in script
    assert "python3 probe.py eth2 02:00:00:00:00:42 192.168.1.72 3" in script
    assert "python3 probe.py eth3 02:00:00:00:00:43 192.168.1.72 3" in script
    assert script.count("tcpdump -p -nn -e -U -i") == 2
    assert "'arp or ether host 02:00:00:00:00:42 or ether host 02:00:00:00:00:43'" in script
    bad = [
        dict(probes=[("eth2", "00:11:22:33:44:55")]),  # globally administered
        dict(probes=[("eth2", "02:00:00:00:00:42"), ("eth3", "02:00:00:00:00:42")]),
        dict(target_ip="not-an-ip"),
        dict(count=0),
        dict(capture_interfaces=["eth2;id"]),
    ]
    for override in bad:
        kwargs = dict(
            probes=[("eth2", "02:00:00:00:00:42")],
            capture_interfaces=["eth2"],
            target_ip="192.168.1.72",
            count=3,
            settle=3,
        )
        kwargs.update(override)
        with pytest.raises((ValueError, ipaddress_error())):
            arp_ingress_probe.remote_script("/tmp/arp-probe-x", **kwargs)


def ipaddress_error():
    import ipaddress

    return ipaddress.AddressValueError
