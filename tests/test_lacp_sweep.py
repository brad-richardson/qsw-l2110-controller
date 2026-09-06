from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest
from test_lan_lacp_evidence import EPOCH, MAPPING, ROUTER, SWITCH, frame, native, pcap
from test_reconcile import current_default_pvids, current_default_vlan, current_lags

from qsw_l2110.errors import ApiError
from tools import lacp_sweep as sweep
from tools.lacp_peer_test import PcapTracker, decode_lacpdu, parse_linux_bond


def ports(*live):
    return {
        f"Port_{p}": {
            "Port_Status": "Enabled",
            "Spd_Duplex_Cfg": "Auto",
            "Flow_Ctrl_Cfg": "On",
            "EEE_Status": "Inactive",
            "Spd_Duplex_Actual": "1000MbpsFull" if p in live else "Link Down",
        }
        for p in range(1, 11)
    }


def options(**values):
    return SimpleNamespace(
        **(
            {
                "ports": tuple(range(1, 9)),
                "management_port": 10,
                "model": "QSW-L2110-10T",
                "firmware": "2.2.3.20260713",
                "group": 4,
                "prepare_vlan": 1,
                "seconds": 30,
                "pdu_grace": 35,
                "poll": 1,
                "debounce": 2,
                "max_minutes": 5,
                "max_trials": 1,
            }
            | values
        )
    )


class FakeSwitch:
    def __init__(self):
        self.lags = current_lags()
        self.vlans = current_default_vlan()
        self.pvids = current_default_pvids()
        self.ports = ports(10)
        self.mirror = {"enabled": False}
        self.writes = []
        self.lag_timeout = None
        self.vlan_timeout = False
        self.corrupt_vlan_on_lag = False

    def get_identity(self):
        return {
            "model": {"model_name": "QSW-L2110-10T"},
            "status": {"fw_ver": "2.2.3.20260713", "sys_macaddr": SWITCH},
        }

    def get_vlan_snapshot(self):
        return copy.deepcopy((self.vlans, self.pvids))

    def get_lag_config(self):
        return copy.deepcopy(self.lags)

    def get_port_settings(self):
        return copy.deepcopy(self.ports)

    def get_port_statistics(self):
        return {"counters": "fixture"}

    def get_json(self, path):
        assert path == "/port_mirror.json"
        return copy.deepcopy(self.mirror)

    def download_backup(self):
        self.writes.append("backup")
        return b"fake opaque backup"

    def set_lag_config(self, payload):
        self.writes.append("lag")
        if self.lag_timeout == "before":
            raise ApiError("write timed out before applying")
        self.lags["system_priority"] = payload["system_priority"]
        for p in range(1, 11):
            for key in self.lags[f"Port_{p}"]:
                if key in payload:
                    self.lags[f"Port_{p}"][key] = payload[key]
        if self.corrupt_vlan_on_lag:
            self.pvids["port_pvids"][1] = 22
        if self.lag_timeout == "after":
            raise ApiError("response timed out after applying")

    def set_vlans(self, payload):
        self.writes.append("vlan")
        changed = {int(v["vlan_id"]): v for v in payload["updatedVlans"]}
        existing = {int(v["vlan_id"]): v for v in self.vlans}
        self.vlans = list((existing | changed).values())
        for p in range(1, 11):
            self.pvids["port_pvids"][p] = next(
                int(v["vlan_id"]) for v in self.vlans if int(v["port_states"][p]) == 1
            )
        if self.vlan_timeout:
            raise ApiError("VLAN response timeout")

    def save(self):
        self.writes.append("save")


@pytest.mark.parametrize("value", ["1,1", "8-1", "0,1", "1,11", "1,9", "1", "x"])
def test_port_pool_rejects_invalid_or_mixed_hardware_classes(value):
    with pytest.raises(ValueError):
        sweep.port_list(value)


def test_detect_pair_and_refuse_ambiguous_or_management_loss():
    assert sweep.detect_pair(ports(1, 4, 10), tuple(range(1, 9)), 10) == (1, 4)
    assert sweep.detect_pair(ports(1, 10), tuple(range(1, 9)), 10) is None
    for live in [(1, 3, 4, 10), (1, 4), (1, 4, 9, 10)]:
        with pytest.raises(RuntimeError):
            sweep.detect_pair(ports(*live), tuple(range(1, 9)), 10)
    unequal = ports(1, 4, 10)
    unequal["Port_4"]["Spd_Duplex_Actual"] = "2500MbpsFull"
    with pytest.raises(RuntimeError, match="equal"):
        sweep.detect_pair(unequal, tuple(range(1, 9)), 10)
    with pytest.raises(ValueError, match="unrecognized"):
        sweep.detect_pair({"Port_1": {}}, tuple(range(1, 9)), 10)


def prepared(tmp_path, **kwargs):
    client = FakeSwitch()
    args = options(**kwargs)
    bench = sweep.Bench(client, args, sweep.Artifacts(tmp_path / "run"))
    bench.prepare()
    return client, args, bench


def test_lag_trials_preserve_management_vlan_and_only_write_lag(tmp_path):
    client, args, bench = prepared(tmp_path)
    before = copy.deepcopy(client.lags["Port_10"])
    for number, pair in enumerate([(1, 2), (1, 4), (7, 8)]):
        bench.apply(sweep.config_for(bench.expected, args, pair), f"trial-{number}")
        enabled = {p for p in range(1, 11) if client.lags[f"Port_{p}"][f"portTypeId_{p}"] == "2"}
        assert enabled == set(pair)
        assert client.lags["Port_10"] == before
        assert client.pvids == current_default_pvids()
    assert client.writes == ["backup", "save", "lag", "lag", "lag"]


@pytest.mark.parametrize("timeout", ["before", "after"])
def test_never_retry_an_ambiguous_lag_write(tmp_path, timeout):
    client, args, bench = prepared(tmp_path)
    client.lag_timeout = timeout
    desired = sweep.config_for(bench.expected, args, (1, 4))
    if timeout == "before":
        with pytest.raises(RuntimeError, match="mismatch"):
            bench.apply(desired, "trial")
    else:
        bench.apply(desired, "trial")
    assert client.writes.count("lag") == 1
    assert client.writes.count("save") == 1  # Only preparation, never failed/running trials.


def test_no_writes_when_configuration_drifts_or_test_cables_are_live(tmp_path):
    client, args, bench = prepared(tmp_path)
    desired = sweep.config_for(bench.expected, args, (1, 4))
    client.mirror["enabled"] = True
    with pytest.raises(RuntimeError, match="outside"):
        bench.apply(desired, "drift")
    client.mirror["enabled"] = False
    client.ports = ports(1, 4, 10)
    with pytest.raises(RuntimeError, match="links must be down"):
        bench.apply(desired, "live")
    assert "lag" not in client.writes


def test_lag_side_effect_on_vlan_aborts_without_trying_to_repair_it(tmp_path):
    client, args, bench = prepared(tmp_path)
    client.corrupt_vlan_on_lag = True
    with pytest.raises(RuntimeError, match="incomplete"):
        bench.apply(sweep.config_for(bench.expected, args, (1, 4)), "bad-vlan")
    assert client.writes == ["backup", "save", "lag"]


def test_preparation_moves_current_pool_to_one_vlan_and_handles_response_timeout(tmp_path):
    client = FakeSwitch()
    client.vlans = [
        {"vlan_id": 1, "vlan_name": "default", "port_states": [0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0]},
        {"vlan_id": 10, "vlan_name": "lan", "port_states": [0, 1, 1, 1, 1, 1, 1, 1, 0, 0, 1]},
        {"vlan_id": 3999, "vlan_name": "wan", "port_states": [0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0]},
    ]
    client.pvids = {"port_pvids": [0, 10, 10, 10, 10, 10, 10, 10, 1, 3999, 10]}
    client.vlan_timeout = True
    bench = sweep.Bench(client, options(), sweep.Artifacts(tmp_path / "run"))
    bench.prepare()
    assert client.pvids["port_pvids"] == [0, 1, 1, 1, 1, 1, 1, 1, 1, 3999, 10]
    assert client.writes == ["backup", "vlan", "save"]


def trackers(when=EPOCH, bad_echo=False):
    result = {}
    for iface, port in MAPPING.items():
        ours = decode_lacpdu(frame(port, True))
        theirs = decode_lacpdu(frame(port, False, mismatch=bad_echo))
        result[iface] = SimpleNamespace(
            sources={"ours": {**ours, "last": when}, "theirs": {**theirs, "last": when}}
        )
    return result


@pytest.mark.parametrize("fault", ["old-trial", "stale", "future", "wrong-echo", "defaulted"])
def test_reciprocal_evidence_rejects_stale_and_mismatched_states(fault):
    n = parse_linux_bond(native(1))
    t = trackers(EPOCH, bad_echo=fault == "wrong-echo")
    since, now = EPOCH, EPOCH + 1
    if fault == "old-trial":
        since = EPOCH + 0.5
    if fault == "stale":
        now = EPOCH + 36
    if fault == "future":
        now = EPOCH - 1
    if fault == "defaulted":
        n["members"]["eth3"]["actor_state"] = 125
    assert not sweep.reciprocal(n, t, MAPPING, SWITCH, ROUTER, since, now)
    assert sweep.reciprocal(
        parse_linux_bond(native(1)), trackers(), MAPPING, SWITCH, ROUTER, EPOCH, EPOCH + 1
    )


def test_mapping_is_derived_from_fresh_switch_pdus_not_adapter_order():
    t = trackers()
    t["eth2"], t["eth3"] = t["eth3"], t["eth2"]
    assert sweep.fresh_mapping({}, t, (3, 4), SWITCH, EPOCH) == {"eth2": 4, "eth3": 3}
    assert sweep.fresh_mapping({}, t, (3, 4), SWITCH, EPOCH + 1) == {}


def test_classification_does_not_turn_a_short_scan_into_stability_proof():
    assert sweep.classification({}, False, False, True) == "NOT_ESTABLISHED_IN_WINDOW"
    assert sweep.classification({}, True, True, True) == "NATIVE_ONLY_NEEDS_MORE_TIME"
    assert sweep.classification({"longest_joint_clean_seconds": 20}, True, False, True).startswith(
        "NEGOTIATED_THEN"
    )
    assert (
        sweep.classification({"current_joint_clean_seconds": 10}, True, True, True) == "NEGOTIATED"
    )
    assert sweep.classification({}, False, False, True, True) == "LINK_CHANGED_INCOMPLETE"


class Clock:
    def __init__(self):
        self.now = float(EPOCH)

    def time(self):
        return self.now

    def sleep(self, value):
        self.now += value


class RecordingPeer:
    def __init__(self, clock, log, ready_at=0):
        self.clock, self.log, self.ready_at = clock, log, ready_at
        self.actor, self.bond = ROUTER, "bond0"
        self.trackers = {i: PcapTracker(log.path / f"{i}.pcap") for i in MAPPING}
        self.records = {i: [] for i in MAPPING}

    def check_management(self):
        pass

    def sample(self):
        self.clock.sleep(0.001)  # Real command/clock samples never share an exact timestamp.
        elapsed = self.clock.now - EPOCH
        raw = native(elapsed)
        with (self.log.path / "bond-states.txt").open("a") as out:
            out.write(raw)
        for iface, port in MAPPING.items():
            # Real incremental PCAP streams; before ready_at the switch echoes unsynced state.
            state = 61 if elapsed >= self.ready_at else 13
            for outbound in (False, True):
                data = bytearray(frame(port, outbound, state=state))
                source = ROUTER if outbound else SWITCH
                data[6:12] = bytes.fromhex(source.replace(":", ""))
                self.records[iface].append((self.clock.now - 0.00001, bytes(data)))
            (self.log.path / f"{iface}.pcap").write_bytes(pcap(self.records[iface]))
            self.trackers[iface].update()
        return parse_linux_bond(raw), self.clock.now


@pytest.mark.parametrize("ready_at,expected_seconds", [(0, 30), (31, 33), (100, 65)])
def test_complete_measurement_with_real_decoder_and_slow_pdu_grace(
    tmp_path, monkeypatch, ready_at, expected_seconds
):
    clock = Clock()
    monkeypatch.setattr(sweep.time, "time", clock.time)
    monkeypatch.setattr(sweep.time, "monotonic", clock.time)
    monkeypatch.setattr(sweep.time, "sleep", clock.sleep)
    client = FakeSwitch()
    client.ports = ports(3, 4, 10)
    log = sweep.Artifacts(tmp_path / "run")
    bench = SimpleNamespace(client=client, log=log, switch=SWITCH)
    peer = RecordingPeer(clock, log, ready_at)
    result = sweep.measure(bench, peer, (3, 4), 1, options())
    assert expected_seconds <= result["seconds"] < expected_seconds + 1.1
    expected = "NATIVE_ONLY_NEEDS_MORE_TIME" if ready_at == 100 else "NEGOTIATED"
    assert result["result"] == expected
    assert result["mapping"] == MAPPING
    assert not result["evaluation"]["pass"]  # The existing evaluator's five-minute gate.


def test_summary_records_incomplete_trials_and_machine_readable_fields(tmp_path):
    log = sweep.Artifacts(tmp_path / "run")
    log.result({"trial": 1, "pair": "1+4", "result": "INCOMPLETE_ERROR_OR_INTERRUPTED"})
    assert json.loads((log.path / "summary.json").read_text())[0]["pair"] == "1+4"
    assert "INCOMPLETE_ERROR" in (log.path / "summary.csv").read_text()


def test_full_schedule_covers_all_28_pairs_with_single_cable_moves():
    from itertools import combinations

    order = sweep.pair_order(tuple(range(1, 9)))
    assert order[0] == (1, 2)
    assert len(order) == 28
    assert set(order) == set(combinations(range(1, 9), 2))
    assert all(len(set(a) & set(b)) == 1 for a, b in zip(order, order[1:], strict=False))
    assert sweep.next_instruction(order, {(1, 2)}, (1, 2)) == (
        "keep port 1 connected; move the cable from port 2 to port 3"
    )
    assert sweep.next_instruction(order, set(order)) == "ALL PAIRS RECORDED"


def test_retained_usb_phy_carrier_only_allows_the_detected_pair(tmp_path):
    client, args, bench = prepared(tmp_path)
    client.ports = ports(1, 4, 10)
    bench.apply(sweep.config_for(bench.expected, args, (1, 4)), "retained", allowed_live=(1, 4))
    client.ports = ports(1, 5, 10)
    with pytest.raises(RuntimeError, match="links must be down"):
        bench.apply(sweep.config_for(bench.expected, args, (1, 4)), "wrong", allowed_live=(1, 4))
    assert client.writes.count("lag") == 1


def test_quiesce_checks_admin_state_even_when_phy_retains_carrier(tmp_path):
    peer = sweep.LinuxPeer(
        ["usbA", "usbB"], "eno1", "https://192.168.1.72", sweep.Artifacts(tmp_path / "run")
    )
    peer.moved = ["usbA", "usbB"]
    links = [{"ifname": i, "flags": ["LOWER_UP"]} for i in peer.moved]
    peer.inside = lambda *args: json.dumps(links)
    peer.assert_down()
    links[1]["flags"].append("UP")
    with pytest.raises(RuntimeError, match="administratively down"):
        peer.assert_down()


def test_mixed_vlans_without_preparation_fail_before_any_write(tmp_path):
    client = FakeSwitch()
    client.pvids["port_pvids"][8] = 10
    bench = sweep.Bench(client, options(prepare_vlan=None), sweep.Artifacts(tmp_path / "run"))
    with pytest.raises(RuntimeError, match="different VLANs"):
        bench.prepare()
    assert client.writes == []


@pytest.mark.parametrize("already_recorded", [0, 7])
def test_guided_sweep_stops_after_every_pair_is_recorded(tmp_path, monkeypatch, already_recorded):
    clock = Clock()
    monkeypatch.setattr(sweep.time, "monotonic", clock.time)
    monkeypatch.setattr(sweep.time, "sleep", clock.sleep)
    monkeypatch.setattr(sweep, "keyboard", lambda: "")
    monkeypatch.setattr(sweep, "config_for", lambda *a: None)
    order = sweep.pair_order(tuple(range(1, 9)))
    client = FakeSwitch()
    client.ports = ports(*order[already_recorded], 10)
    log = sweep.Artifacts(tmp_path / "run")
    for trial, pair in enumerate(order[:already_recorded], 1):
        log.result({"trial": trial, "pair": "+".join(map(str, pair)), "result": "NEGOTIATED"})
    observed = []

    def measure(bench, peer, pair, trial, args, **kwargs):
        observed.append(pair)
        if trial < len(order):
            client.ports = ports(*order[trial], 10)
        return {"trial": trial, "pair": "+".join(map(str, pair)), "result": "NEGOTIATED"}

    monkeypatch.setattr(sweep, "measure", measure)
    bench = SimpleNamespace(
        client=client,
        expected={},
        log=log,
        read=lambda: {},
        check_drift=lambda _: None,
        apply=lambda *a, **kw: None,
    )
    peer = SimpleNamespace(
        interfaces=["usbA", "usbB"],
        check_management=lambda: None,
        check_captures=lambda: None,
        sample=lambda: None,
        carriers=lambda: {"usbA", "usbB"},
        quiesce=lambda: None,
        rebuild=lambda: None,
    )
    sweep.sweep(bench, peer, options(max_trials=50))
    assert observed == order[already_recorded:]
    assert len(log.results) == 28
    assert json.loads((log.path / "coverage.json").read_text())["remaining"] == []


def test_resume_retains_completed_failures_and_retries_interrupted_pairs(tmp_path, monkeypatch):
    args = options(host="https://192.168.1.72", management_interface="eno1")
    path = tmp_path / "previous"
    path.mkdir()
    values = vars(args).copy()
    values["ports"] = list(values["ports"])
    sweep.save(path / "settings.json", values)
    interfaces = ["usbA", "usbB"]
    actor = "02:11:22:33:44:55"
    sweep.save(path / "peer.json", {"interfaces": interfaces, "actor": actor})
    sweep.save(path / "host-before.json", {"interfaces": {i: {"mac": i} for i in interfaces}})
    sweep.save(path / "original-state.json", {"identity": {"status": {"sys_macaddr": SWITCH}}})
    rows = [
        {"trial": 1, "pair": "1+2", "result": "NEGOTIATED"},
        {"trial": 2, "pair": "1+3", "result": "NO_PEER_LACP_IN_WINDOW"},
        {"trial": 3, "pair": "1+4", "result": "INCOMPLETE_ERROR_OR_INTERRUPTED"},
    ]
    sweep.save(path / "summary.json", rows)
    monkeypatch.setattr(
        sweep, "usb_inventory", lambda: [{"interface": i, "mac": i} for i in interfaces]
    )
    loaded = sweep.load_resume(path, args, interfaces)
    assert loaded["actor"] == actor
    assert loaded["switch"] == SWITCH
    assert sweep.completed_pairs(loaded["results"], args.ports) == {(1, 2), (1, 3)}
    args.seconds = 60
    with pytest.raises(ValueError, match="setting differs: seconds"):
        sweep.load_resume(path, args, interfaces)
    args.seconds = 30
    with pytest.raises(ValueError, match="same USB interfaces"):
        sweep.load_resume(path, args, interfaces[::-1])
    monkeypatch.setattr(sweep, "usb_inventory", lambda: [])
    with pytest.raises(ValueError, match="clean up the previous run"):
        sweep.load_resume(path, args, interfaces)


def test_resume_wrong_switch_stops_before_backup_or_configuration(tmp_path):
    client = FakeSwitch()
    args = options(resume_switch="00:00:00:00:00:01")
    bench = sweep.Bench(client, args, sweep.Artifacts(tmp_path / "run"))
    with pytest.raises(RuntimeError, match="different physical switch"):
        bench.prepare()
    assert client.writes == []


def test_high_volume_samples_go_to_separate_files(tmp_path):
    log = sweep.Artifacts(tmp_path / "run")
    log.event("linux_state", links=[])
    log.event("switch_sample", ports={})
    log.event("pair_detected", pair=[1, 2])
    assert '"links"' in (log.path / "linux-counters.jsonl").read_text()
    assert '"ports"' in (log.path / "switch-counters.jsonl").read_text()
    assert '"links"' not in (log.path / "events.jsonl").read_text()


def test_existing_control_verifies_configuration_without_any_switch_write(tmp_path):
    client, args, bench = prepared(tmp_path)
    bench.apply(sweep.config_for(bench.expected, args, (1, 3)), "configure")
    client.ports = ports(1, 3, 10)
    client.writes.clear()
    control = sweep.Bench(client, args, sweep.Artifacts(tmp_path / "control"))
    control.use_existing((1, 3))
    assert client.writes == []
    assert not control.prepared  # Main must not perform switch cleanup writes.
    with pytest.raises(RuntimeError, match="does not match"):
        client.ports = ports(10)
        control.use_existing((1, 2))
    assert client.writes == []


@pytest.mark.parametrize("bad_peer", [False, True])
def test_early_success_and_invalid_peer_guard(tmp_path, monkeypatch, bad_peer):
    clock = Clock()
    monkeypatch.setattr(sweep.time, "time", clock.time)
    monkeypatch.setattr(sweep.time, "monotonic", clock.time)
    monkeypatch.setattr(sweep.time, "sleep", clock.sleep)
    client = FakeSwitch()
    client.ports = ports(3, 4, 10)
    log = sweep.Artifacts(tmp_path / "run")
    bench = SimpleNamespace(client=client, log=log, switch=SWITCH)
    peer = RecordingPeer(clock, log)
    sample = peer.sample

    def changed_sample():
        n, when = sample()
        if bad_peer:
            n["members"]["eth3"]["speed"] = None
            n["members"]["eth3"]["duplex"] = "Unknown"
        return n, when

    peer.sample = changed_sample
    args = options(seconds=15, early_success=True)
    if bad_peer:
        with pytest.raises(RuntimeError, match="check USB drivers"):
            sweep.measure(bench, peer, (3, 4), 1, args)
        assert 5 <= clock.now - EPOCH < 6.1
    else:
        result = sweep.measure(bench, peer, (3, 4), 1, args)
        assert result["result"] == "NEGOTIATED"
        assert 2 <= result["seconds"] < 3.1


def test_capture_startup_requires_up_interfaces_before_bond_creation(tmp_path, monkeypatch):
    log = sweep.Artifacts(tmp_path / "run")
    peer = sweep.LinuxPeer(["usbA", "usbB"], "eno1", "https://192.168.1.72", log)
    operations = []
    up = set()
    monkeypatch.setattr(sweep, "command", lambda args: operations.append(tuple(args)))
    monkeypatch.setattr(sweep.time, "sleep", lambda _: None)

    def inside(*args):
        if args[:4] == ("ip", "link", "set", "dev") and args[-1] == "up":
            up.add(args[4])
        operations.append(args)

    def spawn(args, filename):
        if args[0] == "tcpdump":
            assert args[args.index("-i") + 1] in up
        operations.append(("spawn", filename))

    peer.inside = inside
    peer.spawn = spawn
    peer.check_captures = lambda: operations.append(("captures_verified",))
    peer.rebuild = lambda: operations.append(("bond_created",))
    peer.setup()
    assert operations.index(("captures_verified",)) < operations.index(("bond_created",))
    assert len([o for o in operations if o[0] == "spawn"]) == 3
