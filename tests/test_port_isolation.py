from __future__ import annotations

import copy
import io
import json

import pytest

from tools import port_isolation_agent as agent


@pytest.fixture
def config():
    ports, lags = {}, {"system_priority": "32768"}
    for p in range(1, 11):
        ports[f"Port_{p}"] = dict(
            Port_Status="Enabled",
            Spd_Duplex_Cfg="Auto",
            Flow_Ctrl_Cfg="On",
            EEE_Status="eee_inactive",
            Spd_Duplex_Actual="2500MbpsFull",
        )
        lags[f"Port_{p}"] = {
            f"portTypeId_{p}": "2" if p in (3, 4) else "0",
            f"portPriorityId_{p}": "128",
            f"lacpTimeoutId_{p}": "1",
            f"Port_{p}_grpInd": "4",
            f"Port_{p}_state": "1",
        }
    return dict(
        host="https://switch.test",
        mac="00:00:5e:00:53:86",
        unit="qsw-test",
        mapping={"eth2": 3, "eth3": 4},
        before=dict(ports=ports, lags=lags, pvids=dict(port_pvids=[0] + [10] * 10)),
    )


class FakeClient:
    def __init__(self, config):
        self.config = config
        self.state = copy.deepcopy(config["before"])
        self.calls = []
        self.login_failures = 0
        self.port_read_failures = 0
        self.ambiguous_enable = False

    def login(self):
        self.calls.append(("login",))
        if self.login_failures:
            self.login_failures -= 1
            raise RuntimeError("simulated authentication error")

    def identity(self):
        self.calls.append(("identity",))

    def ports(self):
        if self.port_read_failures:
            self.port_read_failures -= 1
            raise RuntimeError("simulated read error")
        return copy.deepcopy(self.state["ports"])

    def request(self, path):
        return copy.deepcopy(
            self.state[
                {
                    "/port_trunk_cfg.json": "lags",
                    "/all_port_pvid.json": "pvids",
                }[path]
            ]
        )

    def apply(self, port, enabled):
        self.calls.append(("apply", port, enabled))
        self.state["ports"][f"Port_{port}"]["Port_Status"] = "Enabled" if enabled else "Disabled"
        if enabled and self.ambiguous_enable:
            raise RuntimeError("response lost after successful write")


@pytest.mark.parametrize(
    "body,payload,passes",
    [
        (b"", {"port_sts": "Disable"}, True),
        (b"", None, False),
        (b"{}", None, True),
        (b"<html>login</html>", {"port_sts": "Disable"}, False),
        (b'{"redirect":"/login.html"}', {"port_sts": "Disable"}, False),
        (b"[]", {"port_sts": "Disable"}, False),
    ],
)
def test_qss_empty_successful_post_and_invalid_responses(config, body, payload, passes):
    class Opener:
        def open(self, request, **kwargs):
            assert request.get_method() == ("GET" if payload is None else "POST")
            response = io.BytesIO(body)
            response.status = 200
            return response

    client = agent.Client(config, {})
    client.opener = Opener()
    if passes:
        assert client.request("/apply_user_port_setting.json", payload) == {}
        assert client.last_response["body_bytes"] == len(body)
    else:
        with pytest.raises(RuntimeError):
            client.request("/apply_user_port_setting.json", payload)


def test_disable_expected_runtime_changes_do_not_abort(config):
    client = FakeClient(config)
    client.state["ports"]["Port_10"].update(
        Port_Status="Disabled", Spd_Duplex_Actual="Link Down", Flow_Ctrl_Actual="Off"
    )
    client.state["lags"]["Port_10"]["Port_10_state"] = "0"
    agent.verify(client, [10])
    assert not any(c[0] == "apply" for c in client.calls)


@pytest.mark.parametrize("field", ["membership", "pvid", "another_port", "speed"])
def test_actual_configuration_drift_still_aborts(config, field):
    client = FakeClient(config)
    if field == "membership":
        client.state["lags"]["Port_3"]["portTypeId_3"] = "0"
    elif field == "pvid":
        client.state["pvids"]["port_pvids"][3] = 3999
    elif field == "another_port":
        client.state["ports"]["Port_5"]["Port_Status"] = "Disabled"
    else:
        client.state["ports"]["Port_10"]["Spd_Duplex_Cfg"] = "1000MbpsFull"
    with pytest.raises(RuntimeError, match="configuration drift"):
        agent.verify(client)


def test_exact_scoped_payloads_preserve_speed_and_flow(config):
    for port in (10, 5, 6):
        for enabled in (True, False):
            assert agent.port_payload(config["before"], port, enabled) == dict(
                port_sts="Enable" if enabled else "Disable",
                port_spd_duplex="Auto",
                flow_ctrl="On",
                port_num=1,
                port_list=[str(port)],
            )
    for port in (1, 2, 3, 4, 7, 8, 9, 11):
        with pytest.raises(ValueError):
            agent.port_payload(config["before"], port, False)


def test_guard_never_declares_ready_before_authentication(config, tmp_path):
    directory = tmp_path / "port-10"
    directory.mkdir()
    client = FakeClient(config)
    client.login_failures = 1
    with pytest.raises(RuntimeError, match="authentication"):
        agent.guard(directory, config, client)
    assert not (directory / "ready.json").exists()
    assert not any(c[0] == "apply" for c in client.calls)


def test_authenticated_readiness_rehearsal_never_writes_switch(config, tmp_path, monkeypatch):
    directory = tmp_path / "port-10"
    directory.mkdir()
    client = FakeClient(config)
    original = agent.save

    def ready_then_cancel(path, value):
        assert ("login",) in client.calls and ("identity",) in client.calls
        original(path, value)
        (directory / "restore-now").touch()

    monkeypatch.setattr(agent, "save", ready_then_cancel)
    agent.guard(directory, config, client)
    assert (directory / "ready.json").exists()
    assert not any(c[0] == "apply" for c in client.calls)


def test_restore_retries_auth_and_read_errors_then_restores_all(config, tmp_path, monkeypatch):
    client = FakeClient(config)
    for port in (10, 5, 6):
        client.state["ports"][f"Port_{port}"]["Port_Status"] = "Disabled"
    client.login_failures = 2
    client.port_read_failures = 1
    monkeypatch.setattr(agent.time, "sleep", lambda _: None)
    agent.restore_ports(client, [10, 5, 6], tmp_path)
    assert [c for c in client.calls if c[0] == "apply"] == [
        ("apply", 6, True),
        ("apply", 5, True),
        ("apply", 10, True),
    ]
    assert (tmp_path / "restored.json").exists()
    assert client.calls.count(("login",)) == 4


def test_ambiguous_enable_is_verified_without_repeating_successful_write(config, tmp_path):
    client = FakeClient(config)
    client.state["ports"]["Port_10"]["Port_Status"] = "Disabled"
    client.ambiguous_enable = True
    agent.restore_ports(client, [10], tmp_path)
    assert [c for c in client.calls if c[0] == "apply"] == [("apply", 10, True)]


def test_parent_disappearing_triggers_early_restore(config, tmp_path, monkeypatch):
    directory = tmp_path / "port-10"
    directory.mkdir()
    client = FakeClient(config)
    client.state["ports"]["Port_10"]["Port_Status"] = "Disabled"
    agent.save(
        directory / "deadline.json",
        dict(
            deadline_monotonic=agent.time.monotonic() + 60,
            parent_pid=123,
            parent_token="old",
        ),
    )
    monkeypatch.setattr(agent, "parent_token", lambda _: None)
    agent.guard(directory, config, client)
    assert (directory / "restored.json").exists()


@pytest.mark.parametrize("phase", ["port-10", "all-downstream"])
def test_independent_guard_honors_deadline_with_parent_alive(config, tmp_path, monkeypatch, phase):
    directory = tmp_path / phase
    directory.mkdir()
    ports, seconds = agent.PHASES[phase]
    client = FakeClient(config)
    elapsed = [0.0]
    original_save, original_apply = agent.save, client.apply
    monkeypatch.setattr(agent.time, "monotonic", lambda: elapsed[0])
    monkeypatch.setattr(
        agent.time, "sleep", lambda delay: elapsed.__setitem__(0, elapsed[0] + delay)
    )
    monkeypatch.setattr(agent, "parent_token", lambda _: "same-parent")

    def arm_when_ready(path, value):
        original_save(path, value)
        if path.name == "ready.json":
            original_save(
                directory / "deadline.json",
                dict(
                    deadline_monotonic=seconds,
                    parent_pid=123,
                    parent_token="same-parent",
                ),
            )
            for port in ports:
                client.state["ports"][f"Port_{port}"]["Port_Status"] = "Disabled"

    def timed_apply(port, enabled):
        assert enabled and seconds <= elapsed[0] < seconds + 0.1
        return original_apply(port, enabled)

    monkeypatch.setattr(agent, "save", arm_when_ready)
    monkeypatch.setattr(client, "apply", timed_apply)
    agent.guard(directory, config, client)
    assert [c for c in client.calls if c[0] == "apply"] == [
        ("apply", p, True) for p in reversed(ports)
    ]


def test_uncertain_disable_is_not_retried_and_wakes_guard(config, tmp_path, monkeypatch):
    client = FakeClient(config)
    directory = tmp_path / "port-10"

    def start_guard(*args, **kwargs):
        agent.save(directory / "ready.json", dict(pid=123))

    def uncertain_disable(port, enabled):
        assert (directory / "deadline.json").exists()
        client.calls.append(("apply", port, enabled))
        raise RuntimeError("uncertain disable")

    monkeypatch.setattr(agent.subprocess, "run", start_guard)
    monkeypatch.setattr(agent, "parent_token", lambda _: "12345")
    monkeypatch.setattr(client, "apply", uncertain_disable)
    with pytest.raises(RuntimeError, match="uncertain disable"):
        agent.trial(tmp_path, config, "port-10", client)
    assert (directory / "restore-now").exists()
    assert [c for c in client.calls if c[0] == "apply"] == [("apply", 10, False)]


def test_old_ready_files_cannot_be_reused(config, tmp_path):
    (tmp_path / "port-10").mkdir()
    client = FakeClient(config)
    with pytest.raises(FileExistsError):
        agent.trial(tmp_path, config, "port-10", client)
    assert not client.calls


def test_expired_arm_cannot_launch(config, tmp_path):
    agent.save(tmp_path / "armed.json", dict(expires_epoch=0))
    with pytest.raises(RuntimeError, match="expired"):
        agent.run(tmp_path, config, FakeClient(config), "individual")
    assert not (tmp_path / "individual-started.json").exists()


def test_combined_refuses_possible_recovery(config, tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "check_arm", lambda *args: None)
    agent.save(tmp_path / "individual-result.json", dict(complete=True, possible_recovery=True))
    client = FakeClient(config)
    with pytest.raises(RuntimeError, match="without recovery"):
        agent.run(tmp_path, config, client, "combined")
    assert not client.calls


def test_combined_requires_separate_packet_review(config, tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "check_arm", lambda *args: None)
    agent.save(tmp_path / "individual-result.json", dict(complete=True, possible_recovery=False))
    with pytest.raises(RuntimeError, match="evidence review"):
        agent.run(tmp_path, config, FakeClient(config), "combined")


@pytest.mark.parametrize("stage", ["individual", "combined"])
def test_completed_stage_saves_result_and_logs_success(config, tmp_path, monkeypatch, stage):
    monkeypatch.setattr(agent, "check_arm", lambda *args: None)
    monkeypatch.setattr(agent, "observe", lambda *args: False)
    monkeypatch.setattr(agent, "native_state", lambda *args: dict(possible_recovery=False))
    monkeypatch.setattr(
        agent.subprocess, "run", lambda *args, **kwargs: type("Result", (), {"returncode": 0})()
    )
    monkeypatch.setattr(
        agent,
        "trial",
        lambda base, config, phase, client: dict(phase=phase, possible_recovery=False),
    )
    config["recorder_unit"] = "test-recorder"
    config["recorder_remote"] = str(tmp_path)
    for name in ["bond-states.txt", "eth2.pcap", "eth3.pcap"]:
        (tmp_path / name).write_text("fresh fixture")
    if stage == "combined":
        agent.save(
            tmp_path / "individual-result.json", dict(complete=True, possible_recovery=False)
        )
        agent.save(tmp_path / "combined-evidence.json", {})
    agent.run(tmp_path, config, FakeClient(config), stage)
    result = json.loads((tmp_path / (stage + "-result.json")).read_text())
    assert result["complete"] and not result["possible_recovery"]
    events = [json.loads(line) for line in (tmp_path / "journal.jsonl").read_text().splitlines()]
    assert events[-1]["event"] == "stage-complete"
    assert events[-1]["stage"] == stage and events[-1]["complete"]


def native(config, states):
    result = "Bonding Mode: IEEE 802.3ad Dynamic link aggregation\n"
    for iface, port in config["mapping"].items():
        a, p = states[iface]
        result += (
            f"\nSlave Interface: {iface}\nMII Status: up\n"
            f"details actor lacp pdu:\nport state: {a}\n"
            f"details partner lacp pdu:\nport state: {p}\nport number: {port}\n"
            f"system mac address: {config['mac']}\n"
        )
    return result


def test_native_recovery_requires_both_members_without_expired_bits(config):
    assert not agent.native_state(config, native(config, dict(eth2=[61, 61], eth3=[13, 69])))[
        "possible_recovery"
    ]
    assert agent.native_state(config, native(config, dict(eth2=[61, 61], eth3=[61, 61])))[
        "possible_recovery"
    ]
    assert not agent.native_state(config, native(config, dict(eth2=[61, 61], eth3=[189, 61])))[
        "possible_recovery"
    ]
    with pytest.raises(RuntimeError, match="Incomplete"):
        agent.native_state(
            config,
            native(config, dict(eth2=[61, 61], eth3=[61, 61])).replace(
                "MII Status: up", "MII Status: down"
            ),
        )
