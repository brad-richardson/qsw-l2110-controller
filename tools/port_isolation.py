"""Prepare and explicitly start downstream-port isolation with independent router restoration."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shlex
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from tools import port_isolation_agent as agent
from tools.firmware_lab import configuration, credentials, digest
from tools.firmware_runner import Device, Journal, RouterRecorder, exclusive, save
from tools.lan_lacp_evidence import evaluate

REPO = Path(__file__).resolve().parents[1]
DEFAULT_PLAN = REPO / "backups/port-isolation-20260905/plan.json"


def context(directory):
    plan = json.loads((directory / "plan.json").read_text())
    owner = json.loads((directory / "owner.json").read_text())
    router = RouterRecorder(plan, Journal(directory))
    return plan, owner, router


def remote_python(router, script, data):
    source = "import json\nrequest=json.loads(" + repr(json.dumps(data)) + ")\n" + script
    return router.command("sudo -n python3 -", data=source, timeout=45)


def agent_command(owner, mode, *extra):
    return [
        "/usr/bin/python3",
        owner["remote"] + "/agent.py",
        mode,
        "--directory",
        owner["remote"],
        *extra,
    ]


def collect_runtime(router, owner, output):
    raw = remote_python(
        router,
        """
from pathlib import Path
base=Path(request['remote'])
result={}
for path in base.rglob('*'):
 if path.is_file() and path.suffix in ('.json','.jsonl') and path.name!='credentials.json':
  result[str(path.relative_to(base))]=path.read_text()
print(json.dumps(result))
""",
        owner,
    )
    files = json.loads(raw)
    output.mkdir(mode=0o700)
    for name, text in files.items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise RuntimeError("Unexpected evidence path")
        destination = output / relative
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        destination.write_text(text)
    return files


def prepare(directory, base_plan):
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    plan = json.loads(base_plan.read_text())
    journal = Journal(directory)
    router = RouterRecorder(plan, journal)
    unit = "qsw-port-isolation-" + uuid.uuid4().hex[:12]
    owner = dict(unit=unit, remote="/home/pi/lag-recorder/" + unit)
    save(directory / "owner.json", owner)
    try:
        with exclusive():
            device = Device(plan, REPO / ".env", journal)
            before = device.snapshot("before")
            if before["identity"]["status"]["fw_ver"] != "2.2.3.20260713":
                raise RuntimeError("Expected restored 2.2.3 baseline")
            path = device.management_path(before)
            native = router.preflight()
            cfg = configuration(before)
            active = [p for p in range(1, 11) if cfg["lags"][f"portTypeId_{p}"] != "0"]
            if active != [3, 4] or any(
                cfg["lags"][f"Port_{p}_grpInd"] != "4" or cfg["lags"][f"lacpTimeoutId_{p}"] != "1"
                for p in active
            ):
                raise RuntimeError("Production LAG differs from the paused baseline")
            for port in agent.PORTS:
                row = before["ports"][f"Port_{port}"]
                if row["Port_Status"] != "Enabled" or row["Spd_Duplex_Actual"] == "Link Down":
                    raise RuntimeError("A test port is already disabled or disconnected")
            plan["configuration"], plan["configuration_sha256"] = cfg, digest(cfg)
            save(directory / "plan.json", plan)
            save(directory / "readiness.json", dict(management_path=path, router=native))
            router.start(duration=7200)
            config = dict(
                host=plan["host"],
                mac=plan["switch_mac"],
                before=before,
                unit=unit,
                mapping=plan["router"]["mapping"],
                recorder_unit=router.unit,
                recorder_remote=router.remote,
            )
            values = credentials(REPO / ".env")
            secret = {
                name: hashlib.md5(values[key].encode(), usedforsecurity=False).hexdigest()
                for name, key in [("loginusr", "QSW_USER"), ("loginpwd", "QSW_PASSWORD")]
            }
            source = (REPO / "tools/port_isolation_agent.py").read_bytes()
            remote_python(
                router,
                """
import base64,os
from pathlib import Path
os.umask(0o077)
base=Path(request['remote']);base.mkdir(mode=0o700)
(base/'agent.py').write_bytes(base64.b64decode(request['source']))
(base/'config.json').write_text(json.dumps(request['config']))
(base/'credentials.json').write_text(json.dumps(request['secret']))
rehearsal=base/'rehearsal';rehearsal.mkdir(mode=0o700)
(rehearsal/'port-10').mkdir(mode=0o700)
for name in ['agent.py','config.json','credentials.json']:
 (rehearsal/name).write_bytes((base/name).read_bytes())
print('Scoped worker staged; no switch writes')
""",
                {
                    **owner,
                    "source": base64.b64encode(source).decode(),
                    "config": config,
                    "secret": secret,
                },
            )
            # Rehearse the actual detached guardian, stopping it before any deadline is armed.
            rehearsal = dict(unit=unit + "-rehearsal", remote=owner["remote"] + "/rehearsal")
            command = [
                "sudo",
                "-n",
                *agent.guard_service_command(
                    rehearsal["unit"],
                    agent_command(rehearsal, "guard", "--phase", "port-10"),
                    runtime_seconds=60,
                ),
            ]
            router.command(shlex.join(command))
            limit = time.monotonic() + 25
            while True:
                check = json.loads(
                    remote_python(
                        router,
                        """
from pathlib import Path
p=Path(request['remote'])/'port-10'/'ready.json'
print(json.dumps(json.loads(p.read_text()) if p.exists() else None))
""",
                        rehearsal,
                    )
                )
                if check:
                    break
                if time.monotonic() > limit:
                    raise RuntimeError("Detached authentication rehearsal did not become ready")
                time.sleep(1)
            remote_python(
                router,
                """
from pathlib import Path
(Path(request['remote'])/'port-10'/'restore-now').touch()
print('Readiness rehearsed without arming a disable deadline')
""",
                rehearsal,
            )
            time.sleep(1)
            state = router.command(
                shlex.join(
                    [
                        "systemctl",
                        "show",
                        rehearsal["unit"],
                        "--property=ExecMainStatus",
                        "--property=ActiveState",
                        "--property=Result",
                    ]
                )
            ).decode()
            if not all(
                s in state.splitlines()
                for s in ["ExecMainStatus=0", "ActiveState=inactive", "Result=success"]
            ):
                raise RuntimeError("Detached rehearsal did not exit successfully")
            remote_python(
                router,
                """
from pathlib import Path
(Path(request['remote'])/'credentials.json').unlink()
print('Rehearsal credential copy removed')
""",
                rehearsal,
            )
            save(
                directory / "rehearsal.json", dict(ready=check, systemd=state, switch_writes=False)
            )
            armed = json.loads(
                router.command(shlex.join(["sudo", "-n", *agent_command(owner, "arm")])).decode()
            )
            save(directory / "armed.json", armed)
            after = device.snapshot("after-arm")
            if configuration(after) != cfg:
                raise RuntimeError("Configuration changed during preparation")
            for command, stage in [
                ("start-individual.command", "individual"),
                ("start-combined.command", "combined"),
            ]:
                path = directory / command
                path.write_text(
                    "#!/bin/zsh\nset -eu\ncd "
                    + shlex.quote(str(REPO))
                    + "\nexec "
                    + shlex.join(
                        [
                            str(REPO / ".venv/bin/python"),
                            "-m",
                            "tools.port_isolation",
                            "start",
                            "--directory",
                            str(directory),
                            "--stage",
                            stage,
                        ]
                    )
                    + "\n"
                )
                path.chmod(0o700)
            save(
                directory / "preparation-result.json",
                dict(
                    armed=True,
                    configuration_unchanged=True,
                    switch_writes=False,
                    expires_epoch=armed["expires_epoch"],
                    recorder_unit=router.unit,
                    individual_seconds=60,
                    combined_seconds=120,
                ),
            )
    except BaseException:
        if router.started:
            try:
                router.stop()
            except Exception:
                journal.record("preparation-recorder-stop-unavailable")
        # Preparation has no disables; it is safe to remove only our credential copies.
        try:
            remote_python(
                router,
                """
import subprocess
subprocess.run(['systemctl','stop',request['unit']+'-rehearsal'],capture_output=True)
print('Rehearsal stopped before removing its credentials')
""",
                owner,
            )
            remote_python(
                router,
                """
from pathlib import Path
base=Path(request['remote'])
for name in ['credentials.json','rehearsal/credentials.json']:
 p=base/name
 if p.exists():p.unlink()
print('Preparation credential copies removed')
""",
                owner,
            )
        except Exception:
            journal.record("preparation-cleanup-unavailable")
        raise
    return dict(directory=str(directory), armed=armed, switch_writes=False)


def start(directory, stage):
    plan, owner, router = context(directory)
    with exclusive():
        device = Device(plan, REPO / ".env", Journal(directory))
        before = device.snapshot("before-" + stage)
        if configuration(before) != plan["configuration"]:
            raise RuntimeError("Configuration differs from preparation; prepare again")
        router.preflight()
        if stage == "combined":
            evidence = directory / datetime.now(UTC).strftime("individual-review-%Y%m%dT%H%M%SZ")
            files = collect_runtime(router, owner, evidence)
            previous = json.loads(files["individual-result.json"])
            if not previous.get("complete") or previous["possible_recovery"]:
                raise RuntimeError("Individual tests incomplete or recovery requires investigation")
            recorder = json.loads((directory / "recorder.json").read_text())
            router.remote, router.unit = recorder["remote"], recorder["unit"]
            capture = router.fetch(evidence.name + "-packets")
            results = []
            for phase in ["port-10", "port-5", "port-6"]:
                a = json.loads(files[phase + "/deadline.json"])["epoch"]
                b = json.loads(files[phase + "/result.json"])["epoch"]
                result = evaluate(
                    capture, "bond0", plan["router"]["mapping"], plan["switch_mac"], a, b
                )
                if result.get("reason") or result.get("joint_clean_events", 0) > 0:
                    raise RuntimeError(
                        "Recovery or insufficient evidence; inspect individual captures"
                    )
                if result["native_samples"] < (b - a) * 0.8 or any(
                    n < 2 for n in result["packet_counts"].values()
                ):
                    raise RuntimeError("Insufficient individual capture coverage")
                results.append(result)
            remote_python(
                router,
                """
from pathlib import Path
(Path(request['remote'])/'combined-evidence.json').write_text(json.dumps(request['results']))
print('Individual evidence checked for recovery; combined remains a separate launch')
""",
                {**owner, "results": results},
            )
        command = [
            "sudo",
            "-n",
            "systemd-run",
            "--unit=" + owner["unit"] + "-" + stage,
            "--property=RuntimeMaxSec=1800",
            *agent_command(owner, "run", "--stage", stage),
        ]
        # Never retry this launch after an ambiguous SSH result. Inspect its owned unit.
        save(
            directory / (stage + "-launch-intent.json"),
            dict(
                epoch=time.time(),
                unit=owner["unit"] + "-" + stage,
            ),
        )
        router.command(shlex.join(command))
    return dict(stage=stage, launched=True, unit=owner["unit"] + "-" + stage)


def status(directory):
    _, owner, router = context(directory)
    output = directory / datetime.now(UTC).strftime("status-%Y%m%dT%H%M%SZ")
    files = collect_runtime(router, owner, output)
    summaries = {
        name: json.loads(text)
        for name, text in files.items()
        if name
        in (
            "armed.json",
            "individual-result.json",
            "combined-result.json",
        )
    }
    phases = {}
    for phase in agent.PHASES:
        name = phase + "/journal.jsonl"
        if name not in files:
            continue
        rows = [json.loads(line) for line in files[name].splitlines()]
        events = [row for row in rows if row["event"] != "native"]
        native = [row for row in rows if row["event"] == "native"]
        phases[phase] = dict(
            events=events[-6:],
            last_native=native[-1] if native else None,
            restored=phase + "/restored.json" in files,
            complete=phase + "/result.json" in files,
        )
    root = [json.loads(line) for line in files.get("journal.jsonl", "").splitlines()]
    errors = [row for row in root if row["event"] == "failed"]
    return dict(directory=str(output), summaries=summaries, phases=phases, errors=errors)


def finish(directory):
    plan, owner, router = context(directory)
    with exclusive():
        units = [owner["unit"] + "-" + s for s in ["individual", "combined", "rehearsal"]]
        units += [owner["unit"] + "-restore-" + phase for phase in agent.PHASES]
        for unit in units:
            state = (
                router.command(
                    shlex.join(["systemctl", "show", unit, "--property=ActiveState", "--value"])
                )
                .decode()
                .strip()
            )
            if state not in ("inactive", "failed"):
                raise RuntimeError("Test or restoration worker is still active; finish refused")
        device = Device(plan, REPO / ".env", Journal(directory))
        final = device.snapshot("final")
        if configuration(final) != plan["configuration"]:
            raise RuntimeError("Configuration is not fully restored; retain recovery credentials")
        recorder = json.loads((directory / "recorder.json").read_text())
        router.remote, router.unit, router.started = recorder["remote"], recorder["unit"], True
        router.stop()
        capture = router.fetch("final-recorder")
        collect_runtime(router, owner, directory / "final-runtime")
        remote_python(
            router,
            """
from pathlib import Path
p=Path(request['remote'])/'credentials.json'
if p.exists():p.unlink()
print('Owned experiment credential copy removed')
""",
            owner,
        )
        save(directory / "finished.json", dict(epoch=time.time(), configuration_restored=True))
    return dict(finished=True, configuration_restored=True, capture_directory=str(capture))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "start", "status", "finish"])
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--base-plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--stage", choices=["individual", "combined"])
    args = parser.parse_args(argv)
    os.umask(0o077)
    directory = args.directory.resolve()
    if args.command == "prepare":
        result = prepare(directory, args.base_plan)
    elif args.command == "start":
        if args.stage is None:
            parser.error("start requires --stage")
        result = start(directory, args.stage)
    elif args.command == "status":
        result = status(directory)
    else:
        result = finish(directory)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
