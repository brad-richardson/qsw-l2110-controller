from __future__ import annotations

from pathlib import Path

from qsw_l2110.cli import main
from tools.qss_emulator import QssEmulator, QssEmulatorState

EXAMPLE = Path(__file__).parents[1] / "examples" / "firewalla-gold-plus.yaml"


def _args(emulator: QssEmulator, command: str, *rest: str) -> list[str]:
    return ["--host", emulator.base_url, "--allow-http", command, *rest]


def test_cli_applies_and_replans_against_contract_emulator(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("QSW_PASSWORD", "emulator-only")
    with QssEmulator() as emulator:
        assert main(_args(emulator, "plan", "--config", str(EXAMPLE))) == 0
        assert "Planned changes" in capsys.readouterr().out
        assert {method for method, _path in emulator.state.requests} == {"GET"}

        emulator.state.requests.clear()
        assert (
            main(
                _args(
                    emulator,
                    "apply",
                    "--config",
                    str(EXAMPLE),
                    "--backup-dir",
                    str(tmp_path / "backups"),
                    "--yes-i-understand-private-api",
                    "--yes-i-validated-vlan-transitions",
                )
            )
            == 0
        )
        output = capsys.readouterr().out
        assert (
            "Save requested; running configuration verified. Reboot persistence not verified."
        ) in output
        assert emulator.state.actions == ["backup", "set-lags", "set-vlans", "save"]
        assert emulator.state.save_count == 1
        assert emulator.state.persisted_lags == emulator.state.lags
        assert emulator.state.persisted_vlans == emulator.state.vlans
        assert emulator.state.persisted_pvids == emulator.state.pvids
        assert len(list((tmp_path / "backups").glob("*.cfg"))) == 1

        assert main(_args(emulator, "plan", "--config", str(EXAMPLE))) == 0
        assert capsys.readouterr().out.strip() == "No changes."


def test_cli_does_not_save_when_emulator_ignores_vlan_write(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("QSW_PASSWORD", "emulator-only")
    state = QssEmulatorState(converge_vlan_writes=False)
    with QssEmulator(state=state) as emulator:
        result = main(
            _args(
                emulator,
                "apply",
                "--config",
                str(EXAMPLE),
                "--backup-dir",
                str(tmp_path / "backups"),
                "--yes-i-understand-private-api",
                "--yes-i-validated-vlan-transitions",
            )
        )

    captured = capsys.readouterr()
    assert result == 2
    assert "read-back differs" in captured.err
    assert state.actions == ["backup", "set-lags", "set-vlans"]
    assert state.save_count == 0


def test_cli_stops_before_vlan_write_when_emulator_ignores_lag_write(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("QSW_PASSWORD", "emulator-only")
    state = QssEmulatorState(converge_lag_writes=False)
    with QssEmulator(state=state) as emulator:
        result = main(
            _args(
                emulator,
                "apply",
                "--config",
                str(EXAMPLE),
                "--backup-dir",
                str(tmp_path / "backups"),
                "--yes-i-understand-private-api",
                "--yes-i-validated-vlan-transitions",
            )
        )

    captured = capsys.readouterr()
    assert result == 2
    assert "LAG read-back differs" in captured.err
    assert state.actions == ["backup", "set-lags"]
    assert state.save_count == 0


def test_cli_does_not_save_when_vlan_write_does_not_update_pvid(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("QSW_PASSWORD", "emulator-only")
    state = QssEmulatorState(update_pvids_on_vlan_write=False)
    with QssEmulator(state=state) as emulator:
        result = main(
            _args(
                emulator,
                "apply",
                "--config",
                str(EXAMPLE),
                "--backup-dir",
                str(tmp_path / "backups"),
                "--yes-i-understand-private-api",
                "--yes-i-validated-vlan-transitions",
            )
        )

    captured = capsys.readouterr()
    assert result == 2
    assert "pvid:" in captured.err
    assert state.actions == ["backup", "set-lags", "set-vlans"]
    assert state.save_count == 0


def test_cli_surfaces_save_failure_without_claiming_persistence(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("QSW_PASSWORD", "emulator-only")
    state = QssEmulatorState(fail_save=True)
    with QssEmulator(state=state) as emulator:
        result = main(
            _args(
                emulator,
                "apply",
                "--config",
                str(EXAMPLE),
                "--backup-dir",
                str(tmp_path / "backups"),
                "--yes-i-understand-private-api",
                "--yes-i-validated-vlan-transitions",
            )
        )

    captured = capsys.readouterr()
    assert result == 2
    assert "switch returned HTTP 400" in captured.err
    assert "Reboot persistence" not in captured.out
    assert state.actions == ["backup", "set-lags", "set-vlans", "save"]
    assert state.save_count == 0
    assert state.persisted_lags is None


def test_read_only_cli_surfaces_work_against_contract_emulator(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("QSW_PASSWORD", "emulator-only")
    with QssEmulator() as emulator:
        assert main(_args(emulator, "about")) == 0
        assert '"model_name": "QSW-L2110-10T"' in capsys.readouterr().out

        assert main(_args(emulator, "dump-lags")) == 0
        assert '"PortNum": 10' in capsys.readouterr().out

        assert main(_args(emulator, "dump-vlans")) == 0
        assert '"vlan_id": "1"' in capsys.readouterr().out

        output = tmp_path / "factory.cfg"
        assert main(_args(emulator, "backup", str(output))) == 0
        capsys.readouterr()
        assert output.read_bytes().startswith(b"QSS-EMULATOR\x00")
        assert {method for method, _path in emulator.state.requests} == {"GET"}


def test_cli_deletes_tagged_only_vlan_against_contract_emulator(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("QSW_PASSWORD", "emulator-only")
    canary = tmp_path / "canary.yaml"
    canary.write_text(
        "schema_version: 1\n"
        "device:\n  models: [QSW-L2110-10T]\n  firmware: ['2.2.3.20260713']\n  port_count: 10\n"
        "link_aggregation:\n  system_priority: 32768\n  managed_ports: []\n  groups: []\n"
        "vlans:\n  - id: 4093\n    name: api-canary\n    untagged: []\n    tagged: [6]\n",
        encoding="utf-8",
    )
    backups = str(tmp_path / "backups")
    with QssEmulator() as emulator:
        common = ("--backup-dir", backups, "--yes-i-understand-private-api")
        assert main(_args(emulator, "apply", "--config", str(canary), *common)) == 0
        assert [vlan["vlan_id"] for vlan in emulator.state.vlans] == ["1", "4093"]
        capsys.readouterr()

        assert main(_args(emulator, "delete-vlan", "1", *common)) == 2
        assert "VLAN 1" in capsys.readouterr().err

        emulator.state.actions.clear()
        assert main(_args(emulator, "delete-vlan", "4093", *common)) == 0
        assert emulator.state.actions == ["backup", "delete-vlans", "save"]
        assert [vlan["vlan_id"] for vlan in emulator.state.vlans] == ["1"]
        assert emulator.state.persisted_vlans == emulator.state.vlans
        assert "Save requested" in capsys.readouterr().out

        assert main(_args(emulator, "delete-vlan", "4093", *common)) == 0
        assert "not present" in capsys.readouterr().out
        assert len(list((tmp_path / "backups").glob("*.cfg"))) == 2
