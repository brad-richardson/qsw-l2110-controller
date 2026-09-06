"""Scoped port-isolation worker, copied to Firewalla; Python 3.9+ and stdlib only.

Arming and preflight perform no switch writes. A separately launched run is required.
Every disable is preceded by an authenticated, independent restoration service.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import http.cookiejar
import json
import os
import re
import signal
import ssl
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

PORTS = (10, 5, 6)
PHASES = {"port-10": ([10], 60), "port-5": ([5], 60), "port-6": ([6], 60)}
PHASES["all-downstream"] = ([10, 5, 6], 120)
FIELDS = ("Port_Status", "Spd_Duplex_Cfg", "Flow_Ctrl_Cfg", "EEE_Status")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def save(path, value):
    temporary = path.with_name(path.name + "." + str(os.getpid()) + ".tmp")
    with temporary.open("w") as output:
        json.dump(value, output, indent=2)
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(path)


def record(directory, event, **fields):
    row = dict(epoch=time.time(), monotonic=time.monotonic(), event=event, **fields)
    # One append per record, shared by the runner and its restoration worker.
    with (directory / "journal.jsonl").open("a") as output:
        output.write(json.dumps(row) + "\n")
        output.flush()
        os.fsync(output.fileno())


def canonical_ports(ports):
    return {str(p): {k: ports["Port_" + str(p)][k] for k in FIELDS} for p in range(1, 11)}


def canonical_lags(lags):
    result = {"system_priority": str(lags["system_priority"])}
    for port in range(1, 11):
        for key in ("portTypeId_", "portPriorityId_", "lacpTimeoutId_"):
            name = key + str(port)
            result[name] = str(lags["Port_" + str(port)][name])
        name = "Port_" + str(port) + "_grpInd"
        result[name] = str(lags["Port_" + str(port)][name])
    return result


def port_payload(before, port, enabled):
    if port not in PORTS:
        raise ValueError("Port outside the authorized downstream set")
    row = before["ports"]["Port_" + str(port)]
    if row["Port_Status"] != "Enabled":
        raise ValueError("Baseline port must be enabled")
    return dict(
        port_sts="Enable" if enabled else "Disable",
        port_spd_duplex=row["Spd_Duplex_Cfg"],
        flow_ctrl=row["Flow_Ctrl_Cfg"],
        port_num=1,
        port_list=[str(port)],
    )


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise RuntimeError("Unexpected QSS redirect")


class Client:
    def __init__(self, config, secret):
        self.config, self.secret = config, secret
        self.cookies = http.cookiejar.CookieJar()
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            NoRedirect(),
            urllib.request.HTTPCookieProcessor(self.cookies),
            urllib.request.HTTPSHandler(context=context),
        )

    def request(self, path, payload=None):
        try:
            request = urllib.request.Request(
                self.config["host"] + path,
                data=json.dumps(payload).encode() if payload is not None else None,
                headers={
                    "Connection": "close",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
            with self.opener.open(request, timeout=3) as response:
                raw = response.read()
                status = response.status
        except Exception as error:
            raise RuntimeError(
                "QSS transport failed (" + type(error).__name__ + "); credentials redacted"
            ) from None
        if path.startswith("/authorize?"):
            return {}
        self.last_response = dict(path=path, status=status, body_bytes=len(raw))
        # QSS uses HTTP 200 with an empty body for successful port-setting POSTs.
        # Match the repository client's post_json behavior; GETs still require JSON.
        if not raw and payload is not None:
            return {}
        try:
            value = json.loads(raw)
        except ValueError:
            raise RuntimeError("Invalid QSS JSON; body bytes=" + str(len(raw))) from None
        if not isinstance(value, dict) or any(
            k in value for k in ("redirect", "redirect_url", "error", "alert_key")
        ):
            raise RuntimeError("Unexpected QSS response object")
        return value

    def login(self):
        self.cookies.clear()
        self.request("/authorize?" + urllib.parse.urlencode(self.secret))
        if not any(cookie.name == "session" for cookie in self.cookies):
            raise RuntimeError("No authenticated session")

    def identity(self):
        model = self.request("/get_model_name.json")
        status = self.request("/status.json")
        if (
            model.get("model_name") != "QSW-L2110-10T"
            or status.get("sys_macaddr", "").lower() != self.config["mac"]
            or status.get("fw_ver") != "2.2.3.20260713"
        ):
            raise RuntimeError("Switch identity mismatch")

    def ports(self):
        return self.request("/port_setting_load.json")

    def apply(self, port, enabled):
        return self.request(
            "/apply_user_port_setting.json", port_payload(self.config["before"], port, enabled)
        )


def verify(client, disabled=()):
    before = client.config["before"]
    ports = client.ports()
    expected = canonical_ports(before["ports"])
    for port in disabled:
        expected[str(port)]["Port_Status"] = "Disabled"
    if canonical_ports(ports) != expected:
        raise RuntimeError("Port configuration drift")
    if canonical_lags(client.request("/port_trunk_cfg.json")) != canonical_lags(before["lags"]):
        raise RuntimeError("LAG configuration drift")
    if client.request("/all_port_pvid.json") != before["pvids"]:
        raise RuntimeError("PVID configuration drift")
    return ports


def native_state(config, raw=None):
    raw = Path("/proc/net/bonding/bond0").read_text() if raw is None else raw
    members = {}
    for block in re.split(r"\nSlave Interface: ", raw)[1:]:
        name = block.split("\n", 1)[0].strip()
        actor = block.split("details actor lacp pdu:", 1)[-1].split("details partner", 1)[0]
        partner = block.split("details partner lacp pdu:", 1)[-1]
        a = re.search(r"port state: (\d+)", actor)
        p = re.search(r"port state: (\d+)", partner)
        number = re.search(r"port number: (\d+)", partner)
        mac = re.search(r"system mac address: (\S+)", partner)
        if not all((a, p, number, mac)) or "MII Status: up" not in block:
            raise RuntimeError("Incomplete native LACP evidence or member link lost")
        if config["mapping"].get(name) != int(number[1]) or mac[1].lower() != config["mac"]:
            raise RuntimeError("Native member mapping changed")
        members[name] = [int(a[1]), int(p[1])]
    if set(members) != set(config["mapping"]):
        raise RuntimeError("Native bond membership changed")
    clean = all(s & 0x3C == 0x3C and s & 0xC0 == 0 for row in members.values() for s in row)
    return dict(members=members, possible_recovery=clean)


def observe(directory, config, seconds):
    deadline = time.monotonic() + seconds
    possible = False
    while time.monotonic() < deadline:
        state = native_state(config)
        possible = possible or state["possible_recovery"]
        record(directory, "native", **state)
        time.sleep(min(1, max(0, deadline - time.monotonic())))
    return possible


def parent_token(pid):
    try:
        return Path("/proc/" + str(pid) + "/stat").read_text().rsplit(")", 1)[1].split()[19]
    except OSError:
        return None


def restore_ports(client, ports, directory, retry_seconds=240):
    """Keep auth, identity, writes, and readback errors inside the restoration retry loop."""
    deadline = time.monotonic() + retry_seconds
    while time.monotonic() < deadline:
        try:
            client.login()
            client.identity()
            current = client.ports()
            for port in reversed(ports):  # Bring the Mac's port-6 path back first.
                if current["Port_" + str(port)]["Port_Status"] != "Enabled":
                    record(directory, "enable-intent", port=port)
                    try:
                        client.apply(port, True)
                    except Exception:
                        record(directory, "enable-response-uncertain", port=port)
            current = client.ports()
            if all(current["Port_" + str(p)]["Port_Status"] == "Enabled" for p in ports):
                result = dict(epoch=time.time(), ports=current)
                save(directory / "restored.json", result)
                record(directory, "enabled-verified", ports=ports)
                return result
        except Exception as error:
            record(directory, "restore-retry", error_type=type(error).__name__)
        time.sleep(1)
    raise RuntimeError("Restoration unverified; service will retry")


def guard(directory, config, client):
    """Independent service; ready means authenticated, with secrets retained in memory."""
    phase = directory.name
    ports, _ = PHASES[phase]
    if (directory / "restored.json").exists():
        return
    armed = directory / "deadline.json"
    urgent = directory / "restore-now"
    if not armed.exists():
        if urgent.exists():
            return
        client.login()
        client.identity()
        verify(client)
        save(directory / "ready.json", dict(epoch=time.time(), pid=os.getpid()))
        limit = time.monotonic() + 35
        while not armed.exists():
            if urgent.exists() or time.monotonic() > limit:
                return  # No disable was armed, so no restoration write is needed.
            time.sleep(0.05)
    state = json.loads(armed.read_text())
    while time.monotonic() < state["deadline_monotonic"]:
        if urgent.exists() or parent_token(state["parent_pid"]) != state["parent_token"]:
            break
        time.sleep(0.05)
    restore_ports(client, ports, directory)


def guard_service_command(unit, command, runtime_seconds=None):
    result = [
        "systemd-run",
        "--unit=" + unit,
        "--property=Restart=on-failure",
        "--property=RestartSec=2",
        "--property=StartLimitIntervalSec=0",
    ]
    if runtime_seconds is not None:
        result.append("--property=RuntimeMaxSec=" + str(runtime_seconds))
    return result + command


def trial(base, config, phase, client):
    ports, seconds = PHASES[phase]
    directory = base / phase
    directory.mkdir(mode=0o700)  # Never reuse readiness, deadline, or restored flags.
    client.login()
    client.identity()
    verify(client)
    unit = config["unit"] + "-restore-" + phase
    subprocess.run(
        guard_service_command(
            unit,
            [
                "/usr/bin/python3",
                str(base / "agent.py"),
                "guard",
                "--directory",
                str(base),
                "--phase",
                phase,
            ],
        ),
        capture_output=True,
        check=True,
        timeout=10,
    )
    possible = False
    try:
        limit = time.monotonic() + 25
        while not (directory / "ready.json").exists():
            if time.monotonic() > limit:
                raise RuntimeError("Authenticated restoration readiness timed out; no disable")
            time.sleep(0.1)
        ready = json.loads((directory / "ready.json").read_text())
        if parent_token(ready["pid"]) is None:
            raise RuntimeError("Restoration worker exited before disable")
        client.login()
        client.identity()
        verify(client)
        start = time.monotonic()
        save(
            directory / "deadline.json",
            dict(
                epoch=time.time(),
                deadline_monotonic=start + seconds,
                parent_pid=os.getpid(),
                parent_token=parent_token(os.getpid()),
            ),
        )
        for port in ports:
            record(directory, "disable-intent", port=port, duration_seconds=seconds)
            client.apply(port, False)  # Never retry a disable, including ambiguous responses.
            record(directory, "disable-acknowledged", port=port, response=client.last_response)
        save(directory / "down.json", dict(epoch=time.time(), ports=verify(client, ports)))
        possible = observe(directory, config, max(0, start + seconds - time.monotonic()))
    finally:
        (directory / "restore-now").touch()
    limit = time.monotonic() + 300
    while not (directory / "restored.json").exists():
        if time.monotonic() > limit:
            raise RuntimeError("Restoration unverified; sequence stopped, guard continues")
        time.sleep(0.2)
    # A useful recovery signal stops later tests; packet analysis must confirm it.
    possible = observe(directory, config, 90) or possible
    client.login()
    client.identity()
    after = verify(client)
    if any(after["Port_" + str(p)]["Spd_Duplex_Actual"] == "Link Down" for p in ports):
        raise RuntimeError("Restored port has not regained carrier")
    result = dict(epoch=time.time(), phase=phase, possible_recovery=possible, ports=after)
    save(directory / "result.json", result)
    return result


def check_arm(base, config):
    arm = json.loads((base / "armed.json").read_text())
    if (
        time.time() > arm["expires_epoch"]
        or arm["config_sha256"] != digest(config)
        or arm["boot_id"] != Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        or arm["agent_sha256"] != hashlib.sha256((base / "agent.py").read_bytes()).hexdigest()
    ):
        raise RuntimeError("Preparation expired, changed, or router rebooted; prepare afresh")


def run(base, config, client, stage):
    check_arm(base, config)
    if stage == "combined":
        previous = json.loads((base / "individual-result.json").read_text())
        if not previous.get("complete") or previous["possible_recovery"]:
            raise RuntimeError("Combined test requires completed individual tests without recovery")
        if not (base / "combined-evidence.json").exists():
            raise RuntimeError("Combined test requires a separate post-test evidence review")
    claim = base / (stage + "-started.json")
    with claim.open("x") as output:
        json.dump(dict(epoch=time.time()), output)
    client.login()
    client.identity()
    verify(client)
    recorder = subprocess.run(
        ["systemctl", "is-active", "--quiet", config["recorder_unit"]], timeout=5
    )
    if recorder.returncode or native_state(config)["possible_recovery"]:
        raise RuntimeError("Recorder absent or LACP already clean; no isolation test")
    capture = Path(config["recorder_remote"])
    for name, age in [("bond-states.txt", 5), ("eth2.pcap", 40), ("eth3.pcap", 40)]:
        if time.time() - (capture / name).stat().st_mtime > age:
            raise RuntimeError("Recorder evidence is stale; no isolation test")
    if observe(base, config, 35):
        raise RuntimeError("Possible recovery during baseline; no ports disabled")
    phases = list(PHASES)[:3] if stage == "individual" else ["all-downstream"]
    results = []
    for phase in phases:
        result = trial(base, config, phase, client)
        results.append(result)
        if result["possible_recovery"]:
            break
    final = dict(
        complete=len(results) == len(phases),
        results=results,
        possible_recovery=any(r["possible_recovery"] for r in results),
        epoch=time.time(),
    )
    save(base / (stage + "-result.json"), final)
    record(
        base,
        "stage-complete",
        stage=stage,
        **{key: value for key, value in final.items() if key != "epoch"},
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["preflight", "arm", "run", "guard"])
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--phase", choices=list(PHASES))
    parser.add_argument("--stage", choices=["individual", "combined"])
    args = parser.parse_args(argv)
    base = args.directory.resolve()
    os.umask(0o077)
    signal.pthread_sigmask(signal.SIG_SETMASK, [])
    config = json.loads((base / "config.json").read_text())
    secret = json.loads((base / "credentials.json").read_text())
    client = Client(config, secret)
    try:
        if args.mode == "guard":
            if args.phase is None:
                parser.error("guard requires --phase")
            guard(base / args.phase, config, client)
            return 0
        with (base.parent / "port-isolation.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if args.mode == "run":
                if args.stage is None:
                    parser.error("run requires --stage")
                run(base, config, client, args.stage)
            else:
                client.login()
                client.identity()
                verify(client)
                state = native_state(config)
                result = dict(epoch=time.time(), native=state, switch_writes=False)
                if args.mode == "arm":
                    result.update(
                        expires_epoch=time.time() + 3600,
                        config_sha256=digest(config),
                        boot_id=Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
                        agent_sha256=hashlib.sha256((base / "agent.py").read_bytes()).hexdigest(),
                    )
                    save(base / "armed.json", result)
                print(json.dumps(result))
    except Exception as error:
        record(base, "failed", mode=args.mode, error_type=type(error).__name__, reason=str(error))
        if args.mode == "run":
            for phase in PHASES:
                directory = base / phase
                if directory.exists():
                    (directory / "restore-now").touch()
        print(json.dumps(dict(error_type=type(error).__name__, mode=args.mode)))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
