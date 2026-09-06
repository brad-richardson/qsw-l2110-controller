from __future__ import annotations

import argparse
import json
from types import SimpleNamespace

import pytest

from tools import lacp_sweep as sweep
from tools import lacp_ui_sweep as ui

ACTOR = "02:11:22:33:44:55"
SWITCH = "24:5e:be:77:e5:86"


def options(**values):
    return SimpleNamespace(
        **{
            "mode": ui.MODE,
            "ports": (1, 2, 3, 4),
            "interfaces": ["usbA", "usbB"],
            "seconds": 360,
            "switch_mac": SWITCH,
            "switch_timeout": "long",
            "group": 1,
            "management_interface": "eno1",
            "management_target": "1.1.1.1",
            "actor": ACTOR,
        }
        | values
    )


def tracker(system, port, when=1700000000, state=61):
    return SimpleNamespace(
        sources={
            system: {
                "last": when,
                "actor": {"system": system, "port": port, "state": state},
            }
        }
    )


def test_wire_mapping_rejects_reversed_cables_wrong_switch_and_timeout():
    peer = SimpleNamespace(
        actor=ACTOR, trackers={"usbA": tracker(SWITCH, 1), "usbB": tracker(SWITCH, 2)}
    )
    expected = {"usbA": 1, "usbB": 2}
    assert ui.mapping_check(peer, expected, SWITCH, 1700000000, "long")[0] == expected
    with pytest.raises(ui.Incomplete, match="expected 2"):
        peer.trackers["usbB"] = tracker(SWITCH, 3)
        ui.mapping_check(peer, expected, SWITCH, 1700000000, "long")
    with pytest.raises(ui.Incomplete, match="unexpected LACP switch"):
        peer.trackers["usbB"] = tracker("00:00:5e:00:53:01", 2)
        ui.mapping_check(peer, expected, SWITCH, 1700000000, "long")
    with pytest.raises(ui.Incomplete, match="timeout"):
        peer.trackers["usbB"] = tracker(SWITCH, 2, state=63)
        ui.mapping_check(peer, expected, SWITCH, 1700000000, "long")


def test_no_peer_packets_never_infers_mapping_from_an_old_native_state():
    peer = SimpleNamespace(
        actor=ACTOR, trackers={"usbA": tracker(ACTOR, 1), "usbB": tracker(ACTOR, 2)}
    )
    assert ui.mapping_check(peer, {"usbA": 1, "usbB": 2}, SWITCH, 1700000000, "long") == (
        {},
        {"usbA": True, "usbB": True},
    )


class Clock:
    elapsed = 0

    def time(self):
        return 1700000000 + self.elapsed

    def monotonic(self):
        return self.elapsed

    def sleep(self, seconds):
        self.elapsed += seconds


def observation(
    tmp_path,
    monkeypatch,
    clean=lambda _: True,
    evidence=None,
    carriers=lambda _: True,
    failures=lambda _: 0,
    sent=True,
    mapping=True,
):
    clock = Clock()
    monkeypatch.setattr(ui.time, "time", clock.time)
    monkeypatch.setattr(ui.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(ui.time, "sleep", clock.sleep)

    def native():
        return {
            "members": {
                i: {
                    "mii": "up" if carriers(clock.elapsed) else "down",
                    "speed": 1000,
                    "duplex": "full",
                    "link_failures": failures(clock.elapsed),
                }
                for i in ("usbA", "usbB")
            }
        }

    peer = SimpleNamespace(
        interfaces=["usbA", "usbB"],
        actor=ACTOR,
        bond="sweep0",
        log=sweep.Artifacts(tmp_path / "trial"),
        trackers={},
        check_management=lambda: None,
        sample=lambda: (native(), clock.time()),
    )
    monkeypatch.setattr(
        ui,
        "mapping_check",
        lambda *_: (
            {"usbA": 1, "usbB": 2} if mapping else {},
            {"usbA": sent, "usbB": sent},
        ),
    )
    monkeypatch.setattr(sweep, "native_clean", lambda *_: clean(clock.elapsed))
    monkeypatch.setattr(sweep, "reciprocal", lambda *_: clean(clock.elapsed))
    monkeypatch.setattr(
        sweep,
        "evaluate",
        lambda *_: (
            evidence(clock.elapsed)
            if evidence
            else {
                "current_joint_clean_seconds": clock.elapsed,
                "longest_joint_clean_seconds": clock.elapsed,
            }
        ),
    )
    return ui.observe(peer, (1, 2), options(), clock.time()), clock.elapsed


def test_early_establishment_still_gets_the_full_six_minutes(tmp_path, monkeypatch):
    result, elapsed = observation(tmp_path, monkeypatch)
    assert elapsed == 360
    assert result["result"] == "NEGOTIATED"
    assert result["stable_300_seconds_at_end"]


def test_delayed_failure_is_not_masked_by_earlier_success(tmp_path, monkeypatch):
    result, elapsed = observation(
        tmp_path,
        monkeypatch,
        clean=lambda t: t < 230,
        evidence=lambda _: {"current_joint_clean_seconds": 0, "longest_joint_clean_seconds": 229},
    )
    assert elapsed == 360
    assert result["result"] == "NEGOTIATED_THEN_LOST_OR_INCOMPLETE"
    assert not result["stable_300_seconds_at_end"]


def test_missing_peer_is_a_limited_negative_with_unverified_port_mapping(tmp_path, monkeypatch):
    result, elapsed = observation(tmp_path, monkeypatch, clean=lambda _: False, mapping=False)
    assert elapsed == 360
    assert result["result"] == "NO_PEER_LACP_IN_WINDOW"
    assert not result["mapping_verified"]


def test_missing_linux_tx_is_invalid_not_a_switch_failure(tmp_path, monkeypatch):
    with pytest.raises(ui.Incomplete, match="Linux LACP TX"):
        observation(tmp_path, monkeypatch, clean=lambda _: False, sent=False)


@pytest.mark.parametrize(
    "kwargs,match",
    [
        ({"carriers": lambda _: False}, "did not become ready"),
        ({"carriers": lambda t: t < 20}, "carrier lost"),
        ({"failures": lambda t: int(t >= 20)}, "inter-sample cable flap"),
    ],
)
def test_physical_failures_are_incomplete(tmp_path, monkeypatch, kwargs, match):
    with pytest.raises(ui.Incomplete, match=match):
        observation(tmp_path, monkeypatch, **kwargs)


def fake_host(monkeypatch):
    class Peer:
        def __init__(self, interfaces, management, host, log):
            self.interfaces, self.log = interfaces, log
            self.original = {
                i: {"mac": f"00:00:5e:00:53:{n:02x}", "driver": "r8152"}
                for n, i in enumerate(interfaces)
            }
            self.created = False

        def validate(self):
            pass

        def setup(self):
            self.created = True

        def close(self):
            self.created = False
            sweep.save(
                self.log.path / "host-cleanup.json",
                {"errors": [], "capture_exit_codes": [0, 0, -2]},
            )
            return []

    monkeypatch.setattr(ui, "ManualPeer", Peer)
    monkeypatch.setattr(ui, "capture_integrity", lambda _: True)
    monkeypatch.setattr(sweep, "return_artifact_ownership", lambda _: None)
    monkeypatch.setattr(ui.os, "geteuid", lambda: 0)
    monkeypatch.setattr(ui.os, "umask", lambda _: None)
    monkeypatch.setattr(
        sweep, "QswL2110Client", lambda *_a, **_k: pytest.fail("switch session opened")
    )
    monkeypatch.setattr(sweep, "credentials", lambda *_: pytest.fail("credentials read"))
    return Peer


def test_manual_six_pair_sequence_never_opens_a_switch_session(tmp_path, monkeypatch):
    fake_host(monkeypatch)
    observed = []

    def measure(peer, pair, args, since):
        observed.append((pair, args.actor))
        return {"result": "NEGOTIATED", "seconds": 360, "stable_300_seconds_at_end": True}

    monkeypatch.setattr(ui, "observe", measure)
    monkeypatch.setattr("builtins.input", lambda: "")
    output = tmp_path / "run"
    assert ui.main(["--bench-isolated", "--output", str(output)]) == 0
    assert [p for p, _ in observed] == [(1, 2), (1, 3), (1, 4), (2, 4), (2, 3), (3, 4)]
    assert len({a for _, a in observed}) == 1
    assert len(list(output.glob("trial-*/host-cleanup.json"))) == 6
    assert len(json.loads((output / "summary.json").read_text())) == 6
    assert json.loads((output / "coverage.json").read_text())["remaining"] == []


def test_resume_retains_actor_and_skips_measured_pairs(tmp_path, monkeypatch):
    fake_host(monkeypatch)
    calls = []
    monkeypatch.setattr(
        ui,
        "observe",
        lambda peer, pair, args, since: (
            calls.append((pair, args.actor)) or {"result": "NOT_ESTABLISHED_IN_WINDOW"}
        ),
    )
    answers = iter(["", "q"])
    monkeypatch.setattr("builtins.input", lambda: next(answers))
    first, second = tmp_path / "first", tmp_path / "second"
    assert ui.main(["--bench-isolated", "--output", str(first)]) == 0
    monkeypatch.setattr("builtins.input", lambda: "")
    assert ui.main(["--bench-isolated", "--resume", str(first), "--output", str(second)]) == 0
    assert len(calls) == 6
    assert calls[0][0] == (1, 2) and calls[1][0] == (1, 3)
    assert len({actor for _, actor in calls}) == 1
    rows = json.loads((second / "summary.json").read_text())
    assert rows[0]["source_run"].startswith(str(first))
    assert rows[-1]["trial"] == 6


def test_interrupt_records_incomplete_and_runs_cleanup(tmp_path, monkeypatch):
    fake_host(monkeypatch)
    monkeypatch.setattr(ui, "observe", lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    monkeypatch.setattr("builtins.input", lambda: "")
    output = tmp_path / "run"
    assert ui.main(["--bench-isolated", "--output", str(output)]) == 2
    rows = json.loads((output / "summary.json").read_text())
    assert rows[0]["result"] == "INCOMPLETE"
    assert list(output.glob("trial-*/host-cleanup.json"))
    assert json.loads((output / "coverage.json").read_text())["completed"] == []


def test_cleanup_failure_stops_before_any_next_pair(tmp_path, monkeypatch):
    peer_class = fake_host(monkeypatch)
    monkeypatch.setattr(peer_class, "close", lambda _: ["adapter was removed"])
    monkeypatch.setattr(
        ui, "observe", lambda *_: {"result": "NEGOTIATED", "stable_300_seconds_at_end": True}
    )
    monkeypatch.setattr("builtins.input", lambda: "")
    output = tmp_path / "run"
    assert ui.main(["--bench-isolated", "--output", str(output)]) == 2
    rows = json.loads((output / "summary.json").read_text())
    assert len(rows) == 1 and rows[0]["result"] == "INCOMPLETE_CLEANUP"
    assert not rows[0]["stable_300_seconds_at_end"]


def test_ready_pair_selection_requires_a_second_confirmation(tmp_path, monkeypatch):
    answers = iter(["2+4", ""])
    monkeypatch.setattr("builtins.input", lambda: next(answers))
    log = sweep.Artifacts(tmp_path / "run")
    assert ui.ready(log, (1, 2), options()) == (2, 4)
    assert "usbA -> 2; usbB -> 4" in (log.path / "instructions.txt").read_text()


@pytest.mark.parametrize(
    "flags,addresses,master,valid",
    [
        (["LOWER_UP"], [], None, True),
        (["UP", "LOWER_UP"], [], None, False),
        ([], [{"local": "192.0.2.1"}], None, False),
        ([], [], "another-bond", False),
    ],
)
def test_preflight_permits_cabled_down_nics_but_rejects_in_use_interfaces(
    tmp_path, monkeypatch, flags, addresses, master, valid
):
    commands = []

    def command(argv):
        commands.append(argv)
        return json.dumps(
            [
                {
                    "address": "00:00:5e:00:53:01",
                    "flags": flags,
                    "addr_info": addresses,
                    "master": master,
                }
            ]
        )

    monkeypatch.setattr(sweep, "command", command)
    monkeypatch.setattr(
        sweep,
        "usb_inventory",
        lambda: [
            {"interface": i, "mac": "00:00:5e:00:53:01", "driver": "r8152"}
            for i in ("usbA", "usbB")
        ],
    )
    monkeypatch.setattr(ui.ManualPeer, "check_management", lambda _: None)
    peer = ui.ManualPeer(
        ["usbA", "usbB"], "eno1", "http://1.1.1.1", sweep.Artifacts(tmp_path / "log")
    )
    if valid:
        peer.validate()
    else:
        with pytest.raises(ValueError, match="DOWN, unenslaved and unaddressed"):
            peer.validate()
    assert all(argv[:5] == ["ip", "-j", "address", "show", "dev"] for argv in commands)


def test_capture_loss_does_not_complete_coverage(tmp_path, monkeypatch):
    fake_host(monkeypatch)
    monkeypatch.setattr(
        ui, "observe", lambda *_: {"result": "NEGOTIATED", "stable_300_seconds_at_end": True}
    )
    monkeypatch.setattr(ui, "capture_integrity", lambda _: False)
    answers = iter(["", "q"])
    monkeypatch.setattr("builtins.input", lambda: next(answers))
    output = tmp_path / "run"
    assert ui.main(["--bench-isolated", "--output", str(output)]) == 0
    rows = json.loads((output / "summary.json").read_text())
    assert rows[0]["result"] == "INCOMPLETE_CAPTURE"
    assert not rows[0]["stable_300_seconds_at_end"]
    assert json.loads((output / "coverage.json").read_text())["completed"] == []


@pytest.mark.parametrize(
    "change", [{"seconds": 90}, {"actor": "02:aa:bb:cc:dd:ee"}, {"mode": "old-auto-sweep"}]
)
def test_resume_refuses_changed_experiment_settings(tmp_path, change):
    args = options()
    old = vars(args) | {"ports": list(args.ports)}
    sweep.save(tmp_path / "settings.json", old)
    sweep.save(tmp_path / "summary.json", [])
    sweep.save(tmp_path / "host-identity.json", {})
    with pytest.raises(ValueError, match="resume"):
        ui.resume_data(tmp_path, options(**change))


@pytest.mark.parametrize(
    "extra", [["--ports", "9,10"], ["--interfaces", "eno1", "usbA"], ["--seconds", "nan"]]
)
def test_invalid_scope_rejected_before_hardware(tmp_path, monkeypatch, extra):
    monkeypatch.setattr(ui.os, "geteuid", lambda: 0)
    monkeypatch.setattr(ui, "ManualPeer", lambda *_: pytest.fail("hardware accessed"))
    with pytest.raises(SystemExit):
        ui.main(["--bench-isolated", "--output", str(tmp_path / "run"), *extra])


@pytest.mark.parametrize("mac", ["00:00:00:00:00:00", "01:00:5e:00:00:01", "bad"])
def test_invalid_switch_identity(mac):
    with pytest.raises(argparse.ArgumentTypeError):
        ui.switch_address(mac)
