from __future__ import annotations

import argparse
import copy
from types import SimpleNamespace

import pytest
from test_lacp_sweep import FakeSwitch, options, ports

from tools import lacp_recovery as recovery
from tools import lacp_sweep as sweep


def args(**values):
    return options(
        **(
            {
                "prepare_vlan": None,
                "negative_seconds": 90,
                "phase_seconds": 360,
                "max_minutes": 25,
                "recreate_lag": False,
                "offline_observe": False,
            }
            | values
        )
    )


def bench(tmp_path):
    client = FakeSwitch()
    original_identity = client.get_identity
    client.get_identity = lambda: {
        **original_identity(),
        "status": {"fw_ver": "2.2.3.20260713", "sys_macaddr": recovery.SWITCH},
    }
    result = sweep.Bench(client, args(), sweep.Artifacts(tmp_path / "logs"))
    state = result.read()
    desired = sweep.config_for(state, result.args, recovery.PAIR)
    plan = sweep.build_plan(desired, state["lags"], state["vlans"], state["pvids"])
    client.set_lag_config(plan.lag_payload)
    client.ports = ports(1, 7, 10)
    client.writes.clear()
    return result


@pytest.mark.parametrize(
    "value", ["01:12:34:56:78:90", "00:12:34:56:78:90", "bad", "ff:ff:ff:ff:ff:ff"]
)
def test_fixed_actor_rejects_multicast_global_and_malformed(value):
    with pytest.raises(argparse.ArgumentTypeError):
        recovery.actor_address(value)


def test_fixed_actor_accepts_all_locally_administered_unicast_prefixes():
    assert recovery.actor_address("06:AA:BB:CC:DD:EE") == "06:aa:bb:cc:dd:ee"


def test_existing_bench_verifies_identity_and_cabling_without_writes(tmp_path):
    b = bench(tmp_path)
    initial = recovery.verify_bench(b)
    assert recovery.configuration_signature(initial) == recovery.configuration_signature(b.read())
    assert b.client.writes == []
    b.client.ports = ports(1, 7, 9, 10)
    with pytest.raises(RuntimeError, match="unexpected connected"):
        recovery.verify_bench(b)
    b.client.ports = ports(1, 7, 10)
    identity = b.client.get_identity()
    identity["status"]["sys_macaddr"] = "00:00:5e:00:53:01"
    b.client.get_identity = lambda: identity
    with pytest.raises(RuntimeError, match="switch MAC"):
        recovery.verify_bench(b)
    assert b.client.writes == []


def test_restore_readds_removed_lag_preserving_other_ports_and_no_save(tmp_path):
    b = bench(tmp_path)
    initial = recovery.verify_bench(b)
    outside = copy.deepcopy({p: initial["lags"][f"Port_{p}"] for p in (9, 10)})
    b.apply(sweep.config_for(initial, b.args, None), "remove", allowed_live=recovery.PAIR)
    peer = SimpleNamespace(assert_down=lambda: None)
    recovery.restore_switch(b, peer, initial)
    assert recovery.configuration_signature(b.read()) == recovery.configuration_signature(initial)
    assert {p: b.read()["lags"][f"Port_{p}"] for p in (9, 10)} == outside
    assert b.client.writes == ["lag", "lag"]


def test_restore_refuses_to_overwrite_unrelated_configuration_drift(tmp_path):
    b = bench(tmp_path)
    initial = recovery.verify_bench(b)
    b.apply(sweep.config_for(initial, b.args, None), "remove", allowed_live=recovery.PAIR)
    b.client.ports["Port_10"]["Flow_Ctrl_Cfg"] = "Off"
    with pytest.raises(RuntimeError, match="changed outside"):
        recovery.restore_switch(b, SimpleNamespace(assert_down=lambda: None), initial)
    assert b.client.writes == ["lag"]


def test_member_pulse_restores_admin_state_after_interruption(tmp_path, monkeypatch):
    calls = []

    def inside(*argv):
        calls.append(argv)
        return '[{"flags": []}]' if "show" in argv else ""

    peer = SimpleNamespace(
        interfaces=["usbA", "usbB"],
        inside=inside,
        sample=lambda: None,
        log=sweep.Artifacts(tmp_path / "logs"),
    )
    monkeypatch.setattr(
        recovery.time, "sleep", lambda _: (_ for _ in ()).throw(KeyboardInterrupt())
    )
    with pytest.raises(KeyboardInterrupt):
        recovery.pulse_member(peer, {"usbA": 1, "usbB": 7}, float("inf"))
    assert calls[0] == ("ip", "link", "set", "dev", "usbB", "down")
    assert calls[-1] == ("ip", "link", "set", "dev", "usbB", "up")
    assert all("usbA" not in call for call in calls)


def test_member_pulse_refuses_unknown_member_without_action(tmp_path):
    peer = SimpleNamespace(interfaces=["usbA", "usbB"], inside=lambda *_: pytest.fail("write"))
    with pytest.raises(RuntimeError, match="cannot identify"):
        recovery.pulse_member(peer, {}, float("inf"))


class Clock:
    elapsed = 0

    def monotonic(self):
        return self.elapsed

    def time(self):
        return 1700000000 + self.elapsed

    def sleep(self, seconds):
        self.elapsed += seconds


def observation(
    tmp_path,
    monkeypatch,
    joint,
    evidence,
    stop_at=1000,
    member_state=lambda _: "up",
    phase="test",
    failures=lambda _iface, _t: 0,
):
    clock = Clock()
    monkeypatch.setattr(recovery.time, "time", clock.time)
    monkeypatch.setattr(recovery.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(recovery.time, "sleep", clock.sleep)
    log = sweep.Artifacts(tmp_path / "logs")

    # An offline observation must never call any switch method.
    class ForbiddenSwitch:
        def __getattr__(self, name):
            pytest.fail(f"offline capture accessed switch: {name}")

    b = SimpleNamespace(log=log, client=ForbiddenSwitch(), switch=recovery.SWITCH)
    peer = SimpleNamespace(
        check_management=lambda: None,
        sample=lambda: (
            {
                "members": {
                    iface: {
                        "mii": member_state(clock.elapsed),
                        "speed": 1000,
                        "duplex": "full",
                        "link_failures": failures(iface, clock.elapsed),
                    }
                    for iface in ("usbA", "usbB")
                }
            },
            clock.time(),
        ),
        trackers={"usbA": SimpleNamespace(sources={}), "usbB": SimpleNamespace(sources={})},
        bond="sweep0",
        actor=recovery.ACTOR,
    )
    monkeypatch.setattr(sweep, "fresh_mapping", lambda *_: {"usbA": 1, "usbB": 7})
    monkeypatch.setattr(sweep, "reciprocal", lambda *_: joint(clock.elapsed))
    monkeypatch.setattr(sweep, "evaluate", lambda *_: evidence(clock.elapsed))
    result = recovery.observe(b, peer, phase, args(offline_observe=True), stop_at)
    return result, clock.elapsed


def test_negative_window_is_bounded_without_switch_polling(tmp_path, monkeypatch):
    result, seconds = observation(tmp_path, monkeypatch, lambda _: False, lambda _: {})
    assert seconds == 90
    assert result["result"] == "NO_PEER_LACP_IN_WINDOW"


def test_transient_recovery_never_passes_from_historical_peak(tmp_path, monkeypatch):
    result, seconds = observation(
        tmp_path,
        monkeypatch,
        lambda t: 10 <= t < 20,
        lambda _: {"longest_joint_clean_seconds": 320, "current_joint_clean_seconds": 0},
    )
    assert seconds == 360
    assert result["result"] == "TRANSIENT_OR_INCOMPLETE"


def test_sustained_recovery_requires_300_seconds_current_evidence(tmp_path, monkeypatch):
    result, seconds = observation(
        tmp_path,
        monkeypatch,
        lambda t: t >= 30,
        lambda t: {"current_joint_clean_seconds": max(0, t - 30)},
    )
    assert seconds == 330
    assert result["result"] == "SUSTAINED_RECOVERY"


def test_total_deadline_limits_extended_observation(tmp_path, monkeypatch):
    result, seconds = observation(tmp_path, monkeypatch, lambda _: True, lambda _: {}, stop_at=45)
    assert seconds == 45
    assert result["deadline_reached"]
    assert result["result"] != "SUSTAINED_RECOVERY"


def test_lost_carrier_stops_as_incomplete_instead_of_recovery_failure(tmp_path, monkeypatch):
    with pytest.raises(RuntimeError, match="carrier lost"):
        observation(
            tmp_path,
            monkeypatch,
            lambda _: False,
            lambda _: {},
            member_state=lambda t: "up" if t < 10 else "down",
        )


def test_never_ready_carriers_do_not_count_as_lacp_failure(tmp_path, monkeypatch):
    with pytest.raises(RuntimeError, match="never became ready"):
        observation(
            tmp_path, monkeypatch, lambda _: False, lambda _: {}, member_state=lambda _: "down"
        )


def test_brief_flap_between_samples_is_incomplete_despite_mii_staying_up(tmp_path, monkeypatch):
    with pytest.raises(RuntimeError, match="link-failure counter changed"):
        observation(
            tmp_path,
            monkeypatch,
            lambda _: True,
            lambda t: {"current_joint_clean_seconds": t},
            failures=lambda iface, t: 1 if iface == "usbB" and t >= 10 else 0,
        )
    import json

    result = json.loads((tmp_path / "logs" / "summary.json").read_text())[-1]
    assert result["result"] == "INCOMPLETE_LINK_CHANGED"


def test_failure_count_from_prior_intentional_pulse_is_new_phase_baseline(tmp_path, monkeypatch):
    result, seconds = observation(
        tmp_path,
        monkeypatch,
        lambda _: True,
        lambda t: {"current_joint_clean_seconds": t},
        failures=lambda iface, _t: 3 if iface == "usbB" else 0,
    )
    assert seconds == 300
    assert result["result"] == "SUSTAINED_RECOVERY"
    assert result["link_failure_baseline"] == {"usbA": 0, "usbB": 3}


@pytest.mark.parametrize(
    "phase,expected",
    [("baseline", "BASELINE_HEALTHY"), ("offline_observe", "SUSTAINED_NEGOTIATION")],
)
def test_observation_success_is_not_claimed_as_recovery(tmp_path, monkeypatch, phase, expected):
    result, _ = observation(
        tmp_path,
        monkeypatch,
        lambda _: True,
        lambda t: {"current_joint_clean_seconds": t},
        phase=phase,
    )
    assert result["result"] == expected


def test_no_more_actions_after_first_sustained_recovery(tmp_path, monkeypatch):
    b = bench(tmp_path)
    recovery.verify_bench(b)
    peer = SimpleNamespace(actor=recovery.ACTOR, rebuild=lambda: pytest.fail("unexpected rebuild"))
    monkeypatch.setattr(recovery, "observe", lambda *_: {"result": "BASELINE_HEALTHY"})
    monkeypatch.setattr(recovery, "pulse_member", lambda *_: pytest.fail("unexpected pulse"))
    result = recovery.run_phases(b, peer, args(recreate_lag=True), float("inf"))
    assert result["result"] == "BASELINE_HEALTHY"
    assert b.client.writes == []


def test_peer_validation_failure_still_cleans_owned_resources(tmp_path):
    log = sweep.Artifacts(tmp_path / "logs")
    calls = []
    peer = SimpleNamespace(
        validate=lambda: (_ for _ in ()).throw(RuntimeError("invalid peer")),
        close=lambda: calls.append("close") or [],
    )
    assert recovery.execute(args(), log, peer) == 1
    assert calls == ["close"]


def test_switch_restore_failure_still_closes_peer(tmp_path, monkeypatch):
    b = bench(tmp_path)
    calls = []

    class Session:
        def __enter__(self):
            return b.client

        def __exit__(self, *_):
            return None

    b.client.authenticate = lambda *_: None
    peer = SimpleNamespace(
        validate=lambda: None,
        original={},
        created=True,
        setup=lambda: None,
        quiesce=lambda: calls.append("quiesce"),
        close=lambda: calls.append("close") or [],
    )
    monkeypatch.setattr(sweep, "QswL2110Client", lambda *_a, **_k: Session())
    monkeypatch.setattr(sweep, "credentials", lambda _: ("fixture", "fixture"))
    monkeypatch.setattr(recovery, "run_phases", lambda *_: None)
    monkeypatch.setattr(
        recovery, "restore_switch", lambda *_: (_ for _ in ()).throw(RuntimeError("fixture drift"))
    )
    a = args(host="https://fixture", insecure=True, env_file=tmp_path / ".env")
    assert recovery.execute(a, b.log, peer) == 1
    assert calls == ["quiesce", "close"]
