from __future__ import annotations

import hashlib
import json
from contextlib import nullcontext
from copy import deepcopy

import httpx
import pytest

from tools import firmware_runner as runner
from tools.firmware_lab import configuration, digest


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(runner.time, "sleep", lambda _: None)
    monkeypatch.setattr(runner, "ping_gateway", lambda: True)
    raw = {"PortNum": 10, "system_priority": 32768}
    ports = {}
    for p in range(1, 11):
        raw[f"Port_{p}"] = {
            f"portTypeId_{p}": 2 if p < 5 else 0,
            f"portPriorityId_{p}": 128,
            f"lacpTimeoutId_{p}": 1 if p < 5 else 0,
            f"Port_{p}_grpInd": 1 if p < 3 else 4 if p < 5 else 0,
        }
        ports[f"Port_{p}"] = dict(
            Port_Status="Enabled",
            Spd_Duplex_Cfg="Auto",
            Flow_Ctrl_Cfg="On",
            EEE_Status="eee_inactive",
        )
    snapshot = dict(
        identity=dict(
            model=dict(model_name="QSW-L2110-10T"),
            status=dict(fw_ver=runner.BASELINE, sys_macaddr="00:00:5e:00:53:86"),
        ),
        lags=raw,
        ports=ports,
        links={},
        lag_status={},
        vlans=[dict(vlan_id=1, vlan_name="default", port_states=[0] + [1] * 10)],
        pvids=dict(port_pvids=[0] + [1] * 10),
    )
    inventory, images = {}, {}
    for version in runner.SEQUENCE:
        data = (version.encode() * 2000)[:24009]
        name = version + ".img"
        (tmp_path / name).write_bytes(data)
        images[version] = data
        inventory[version] = dict(
            firmware=version, filename=name, size=len(data), sha256=hashlib.sha256(data).hexdigest()
        )
    monkeypatch.setattr(runner, "catalog", lambda: inventory)
    cfg = deepcopy(configuration(snapshot))
    plan = dict(
        schema_version=1,
        model="QSW-L2110-10T",
        host="https://switch.test",
        switch_mac="00:00:5e:00:53:86",
        insecure=False,
        images=inventory,
        image_directory=str(tmp_path),
        configuration=cfg,
        configuration_sha256=digest(cfg),
        sequence=runner.SEQUENCE,
        observation_seconds=450,
        boot_timeout_seconds=600,
        router=dict(mapping={"eth2": 3, "eth3": 4}, bond="bond0"),
    )
    env = tmp_path / ".env"
    env.write_text("QSW_HOST=https://switch.test\nQSW_USER=test\nQSW_PASSWORD=never-log-this\n")
    journal = runner.Journal(tmp_path)
    return plan, snapshot, images, env, journal


def test_corrupt_restore_image_blocks_before_any_network(fixture):
    plan, _, _, _, journal = fixture
    (journal.directory / plan["images"][runner.BASELINE]["filename"]).write_bytes(b"corrupt")
    with pytest.raises(Exception, match="mismatch"):
        runner.validate_plan(plan)


@pytest.mark.parametrize("fault", [None, "lost-chunk", "no-success", "precheck", "redirect"])
def test_real_upload_transport_exact_bytes_and_no_ambiguous_retries(fixture, fault):
    plan, snapshot, images, env, journal = fixture
    target = runner.SEQUENCE[0]
    received, requests = bytearray(), []
    version = runner.BASELINE

    def handler(request):
        nonlocal version
        path = request.url.path
        requests.append((request.method, path))
        if path == "/authorize":
            return httpx.Response(200, json={}, headers={"set-cookie": "session=test; Path=/"})
        if path == "/get_model_name.json":
            return httpx.Response(200, json=snapshot["identity"]["model"])
        if path == "/status.json":
            return httpx.Response(200, json={**snapshot["identity"]["status"], "fw_ver": version})
        if path == "/fwupdate_reboot_check.json":
            return httpx.Response(200, json={"alert_key": "busy"} if fault == "precheck" else {})
        assert path == "/firmware/upgrade"
        assert request.headers.get("content-type", "") != "multipart/form-data"
        assert request.headers["cookie"] == "session=test"
        received.extend(request.content)
        if fault == "lost-chunk" and len(received) > 12000:
            raise httpx.ReadTimeout("lost response", request=request)
        if fault == "redirect":
            return httpx.Response(302, headers={"location": "/login.html"})
        if len(received) == len(images[target]):
            version = target
            return httpx.Response(
                200, text="complete" if fault == "no-success" else "Verification: Success\n"
            )
        return httpx.Response(200, text="chunk received")

    device = runner.Device(plan, env, journal, transport=httpx.MockTransport(handler))
    if fault is None:
        device.flash(target, images[target])
        assert received == images[target]
        assert journal.state == "known-build"
        assert requests.count(("POST", "/firmware/upgrade")) == 3
    else:
        with pytest.raises(runner.StopRun):
            device.flash(target, images[target])
        assert journal.state == "firmware-write-pending"
        expected = {"lost-chunk": 2, "no-success": 3, "precheck": 0, "redirect": 1}[fault]
        assert requests.count(("POST", "/firmware/upgrade")) == expected
    assert "never-log-this" not in (journal.directory / "journal.jsonl").read_text()


class FakeRouter:
    started = False
    unit = "test-unit"

    def preflight(self):
        pass

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def fetch(self, label):
        raise runner.StopRun("Synthetic recorder has no physical captures")

    def command(self, command):
        pass


class FakeDevice:
    def __init__(self, plan, snapshot, journal, fault=None):
        self.plan, self.snapshot_data, self.journal = plan, deepcopy(snapshot), journal
        self.fault, self.uploads = fault, []

    def snapshot(self, label):
        return deepcopy(self.snapshot_data)

    def identity(self, identity, version=None):
        found = identity["status"]["fw_ver"]
        if version and found != version:
            raise runner.StopRun("wrong build")
        return found

    def flash(self, target, image):
        self.uploads.append(target)
        if self.fault == "ambiguous":
            self.journal.phase("firmware-write-pending")
            raise runner.AmbiguousWrite("ambiguous")
        self.snapshot_data["identity"]["status"]["fw_ver"] = target

    def repair(self, snapshot, label):
        return snapshot

    def management_path(self, snapshot):
        pass


@pytest.mark.parametrize("fault", [None, "lan-loss", "ambiguous", "stop-request"])
def test_sequence_returns_to_baseline_only_from_known_safe_state(fixture, fault):
    plan, snapshot, images, _, journal = fixture
    device = FakeDevice(plan, snapshot, journal, fault)
    router = FakeRouter()

    def observer(device, router, label, seconds):
        if label == "stage-0" and fault == "lan-loss":
            raise runner.LanLost("offline")
        if label == "stage-0" and fault == "stop-request":
            raise runner.ReturnRequested("user")
        return {"pass": False}  # Existing degraded baseline must not abort a comparison.

    if fault == "ambiguous":
        with pytest.raises(runner.AmbiguousWrite):
            runner.run_sequence(plan, images, device, router, observer=observer)
        assert device.uploads == runner.SEQUENCE[:1]
    else:
        result = runner.run_sequence(plan, images, device, router, observer=observer)
        assert result["status"] == ("complete" if fault is None else "returned-early")
        assert device.uploads == (
            runner.SEQUENCE if fault is None else [runner.SEQUENCE[0], runner.BASELINE]
        )
    assert not router.started


@pytest.mark.parametrize("drift", ["ports", "pvids", "extra-vlan"])
def test_unhandled_configuration_drift_never_posts(fixture, drift):
    plan, snapshot, _, env, journal = fixture
    if drift == "ports":
        snapshot["ports"]["Port_6"]["Port_Status"] = "Disabled"
    elif drift == "pvids":
        snapshot["pvids"]["port_pvids"][6] = 10
    else:
        snapshot["vlans"].append(dict(vlan_id=100, vlan_name="unexpected", port_states=[0] * 11))

    def forbidden(request):
        raise AssertionError("Network access before drift gate")

    device = runner.Device(plan, env, journal, transport=httpx.MockTransport(forbidden))
    with pytest.raises(runner.StopRun):
        device.repair(snapshot, "repair")


def test_explicit_arming_and_digest_required(fixture, monkeypatch):
    plan, _, _, _, journal = fixture
    path = journal.directory / "plan-input.json"
    path.write_text(json.dumps(plan))
    with pytest.raises(SystemExit):
        runner.main(
            [
                "start",
                "--plan",
                str(path),
                "--output",
                str(journal.directory / "run"),
                "--plan-sha256",
                digest(plan),
            ]
        )
    with pytest.raises(SystemExit):
        runner.main(
            [
                "start",
                "--plan",
                str(path),
                "--output",
                str(journal.directory / "run"),
                "--plan-sha256",
                "wrong",
                "--yes-flash-shared-switch",
            ]
        )
    assert not (journal.directory / "run").exists()


@pytest.mark.parametrize("broken_readback", [False, True])
def test_repair_restores_lags_vlans_and_pvids_before_save(fixture, monkeypatch, broken_readback):
    plan, current, _, env, journal = fixture
    target = deepcopy(current)
    target["vlans"] = []
    for vid, members in [(1, [8]), (10, [3, 4, 5, 6, 7, 10]), (3999, [1, 2, 9])]:
        states = [0] * 11
        for port in members:
            states[port] = 1
            target["pvids"]["port_pvids"][port] = vid
        target["vlans"].append(dict(vlan_id=vid, vlan_name=str(vid), port_states=states))
    plan["configuration"] = deepcopy(configuration(target))
    plan["configuration_sha256"] = digest(plan["configuration"])
    for port in range(1, 5):
        current["lags"][f"Port_{port}"][f"portTypeId_{port}"] = 0
    events = []

    class Client:
        def get_identity(self):
            return current["identity"]

        def set_lag_config(self, payload):
            events.append("lag-post")
            assert len(payload) == 41
            for p in range(1, 11):
                for key in current["lags"][f"Port_{p}"]:
                    current["lags"][f"Port_{p}"][key] = payload[key]

        def set_vlans(self, payload):
            events.append("vlan-post")
            # Restoring an untagged destination must precede the source default VLAN.
            vids = [int(v["vlan_id"]) for v in payload["updatedVlans"]]
            assert vids.index(10) < vids.index(1) and vids.index(3999) < vids.index(1)
            if not broken_readback:
                current["vlans"] = deepcopy(target["vlans"])
                current["pvids"] = deepcopy(target["pvids"])

        def save(self):
            events.append("save-post")

    device = runner.Device(plan, env, journal)
    monkeypatch.setattr(device, "session", lambda: nullcontext(Client()))

    def snapshot(label):
        events.append("backup-read:" + label)
        return deepcopy(current)

    monkeypatch.setattr(device, "snapshot", snapshot)
    if broken_readback:
        with pytest.raises(runner.StopRun, match="readback"):
            device.repair(deepcopy(current), "repair")
        assert "save-post" not in events
    else:
        final = device.repair(deepcopy(current), "repair")
        assert configuration(final) == plan["configuration"]
        assert events[-2:] == ["save-post", "backup-read:repair-saved"]
    assert events[0] == "backup-read:repair-before-repair"
    assert events.count("lag-post") == 1 and events.count("vlan-post") == 1


def test_management_path_requires_direct_wifi_and_non_lag_ingress(fixture):
    _, snapshot, _, _, _ = fixture
    route = "route to: 192.168.1.72\n  interface: en0\n"
    wifi = "ether 00:00:5e:00:53:aa\n"
    table = dict(batch=[dict(mac_addr="00:00:5e:00:53:aa", portid=6)])
    assert runner.independent_management_port(route, wifi, table, snapshot["lags"]) == 6
    with pytest.raises(runner.StopRun, match="directly"):
        runner.independent_management_port(
            route + "gateway: 192.168.1.1\n", wifi, table, snapshot["lags"]
        )
    table["batch"][0]["portid"] = 3
    with pytest.raises(runner.StopRun, match="active LAG"):
        runner.independent_management_port(route, wifi, table, snapshot["lags"])


@pytest.mark.parametrize("gateway", [False, True])
def test_router_unavailable_at_stage_boundary_triggers_controlled_return(
    fixture, monkeypatch, gateway
):
    plan, snapshot, _, _, journal = fixture
    device = FakeDevice(plan, snapshot, journal)
    monkeypatch.setattr(device, "status", lambda: {}, raising=False)
    monkeypatch.setattr(runner, "ping_gateway", lambda: gateway)

    def unavailable():
        raise runner.RouterUnavailable("SSH unreachable")

    with pytest.raises(runner.ReturnRequested if gateway else runner.LanLost):
        runner.router_operation(device, unavailable, timeout=0)


def test_router_outage_with_qss_loss_never_requests_a_flash(fixture, monkeypatch):
    plan, snapshot, _, _, journal = fixture
    device = FakeDevice(plan, snapshot, journal)

    def offline():
        raise runner.StopRun("QSS unreachable")

    def unavailable():
        raise runner.RouterUnavailable("SSH unreachable")

    monkeypatch.setattr(device, "status", offline, raising=False)
    with pytest.raises(runner.StopRun, match="QSS unreachable"):
        runner.router_operation(device, unavailable, timeout=0)
    assert device.uploads == []
