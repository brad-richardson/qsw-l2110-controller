from __future__ import annotations

import stat
from argparse import Namespace
from pathlib import Path

import pytest
import yaml
from test_models import desired_mapping
from test_reconcile import current_default_vlan, current_lags

from qsw_l2110.cli import _apply, _delete_vlan, _write_backup
from qsw_l2110.errors import ConfigError, VerificationError


class FakeClient:
    def __init__(
        self,
        *,
        converge_vlans: bool = True,
        mutate_during_backup: bool = False,
        reset_vlans_after_lag: bool = False,
    ) -> None:
        self.lags = current_lags()
        self.vlans = current_default_vlan()
        self.converge_vlans = converge_vlans
        self.mutate_during_backup = mutate_during_backup
        self.reset_vlans_after_lag = reset_vlans_after_lag
        self.actions: list[str] = []

    def get_identity(self) -> dict:
        return {
            "model": {"model_name": "QSW-L2110-10T"},
            "status": {"fw_ver": "2.2.3.20260713"},
        }

    def get_lag_config(self) -> dict:
        return self.lags

    def get_vlans(self) -> list[dict]:
        return self.vlans

    def get_vlan_snapshot(self) -> tuple[list[dict], dict]:
        pvids = [0]
        for port in range(1, 11):
            owner = next(
                int(vlan["vlan_id"]) for vlan in self.vlans if vlan["port_states"][port] == 1
            )
            pvids.append(owner)
        return self.vlans, {
            "port_pvids": pvids,
        }

    def download_backup(self) -> bytes:
        self.actions.append("backup")
        if self.mutate_during_backup:
            self.lags["Port_10"]["portPriorityId_10"] = "129"
        return b"opaque-backup"

    def set_lag_config(self, payload: dict) -> None:
        self.actions.append("lag")
        self.lags["system_priority"] = payload["system_priority"]
        for port in range(1, 11):
            for key in (
                f"portTypeId_{port}",
                f"portPriorityId_{port}",
                f"lacpTimeoutId_{port}",
                f"Port_{port}_grpInd",
            ):
                self.lags[f"Port_{port}"][key] = payload[key]
        if self.reset_vlans_after_lag:
            self.vlans = current_default_vlan()

    def set_vlans(self, payload: dict) -> None:
        self.actions.append("vlan")
        if self.converge_vlans:
            self.vlans = payload["updatedVlans"]

    def save(self) -> None:
        self.actions.append("save")

    def delete_vlan(self, vlan_id: int) -> None:
        self.actions.append(f"delete:{vlan_id}")
        if self.converge_vlans:
            self.vlans = [vlan for vlan in self.vlans if int(vlan["vlan_id"]) != vlan_id]


def write_config(path: Path) -> None:
    path.write_text(yaml.safe_dump(desired_mapping(), sort_keys=False), encoding="utf-8")


def test_apply_backs_up_orders_writes_and_verifies(tmp_path: Path) -> None:
    config_path = tmp_path / "switch.yaml"
    write_config(config_path)
    client = FakeClient()
    args = Namespace(
        config=config_path,
        backup_dir=tmp_path / "backups",
        yes_i_validated_vlan_transitions=True,
    )
    assert _apply(args, client) == 0  # type: ignore[arg-type]
    assert client.actions == ["backup", "lag", "vlan", "save"]
    backups = list((tmp_path / "backups").glob("*.cfg"))
    assert len(backups) == 1
    assert stat.S_IMODE(backups[0].stat().st_mode) == 0o600


def test_apply_never_saves_failed_vlan_readback(tmp_path: Path) -> None:
    config_path = tmp_path / "switch.yaml"
    write_config(config_path)
    client = FakeClient(converge_vlans=False)
    args = Namespace(
        config=config_path,
        backup_dir=tmp_path / "backups",
        yes_i_validated_vlan_transitions=True,
    )
    with pytest.raises(VerificationError, match="read-back differs"):
        _apply(args, client)  # type: ignore[arg-type]
    assert client.actions == ["backup", "lag", "vlan"]


def test_apply_requires_vlan_transition_canary_acknowledgement(tmp_path: Path) -> None:
    config_path = tmp_path / "switch.yaml"
    write_config(config_path)
    client = FakeClient()
    args = Namespace(
        config=config_path,
        backup_dir=tmp_path / "backups",
        yes_i_validated_vlan_transitions=False,
    )
    with pytest.raises(ConfigError, match="disconnected hardware canary"):
        _apply(args, client)  # type: ignore[arg-type]
    assert client.actions == []


def test_apply_aborts_if_state_changes_during_backup(tmp_path: Path) -> None:
    config_path = tmp_path / "switch.yaml"
    write_config(config_path)
    client = FakeClient(mutate_during_backup=True)
    args = Namespace(
        config=config_path,
        backup_dir=tmp_path / "backups",
        yes_i_validated_vlan_transitions=True,
    )
    with pytest.raises(ConfigError, match="state changed while the backup was downloaded"):
        _apply(args, client)  # type: ignore[arg-type]
    assert client.actions == ["backup"]


def test_post_lag_vlan_side_effect_rechecks_transition_gate(tmp_path: Path) -> None:
    config_path = tmp_path / "switch.yaml"
    write_config(config_path)
    desired = desired_mapping()
    client = FakeClient(reset_vlans_after_lag=True)
    client.vlans = [
        {
            "vlan_id": str(vlan["id"]),
            "vlan_name": vlan["name"],
            "port_states": [
                0,
                *[
                    1 if port in vlan["untagged"] else 2 if port in vlan["tagged"] else 0
                    for port in range(1, 11)
                ],
            ],
        }
        for vlan in desired["vlans"]
    ]
    args = Namespace(
        config=config_path,
        backup_dir=tmp_path / "backups",
        yes_i_validated_vlan_transitions=False,
    )
    with pytest.raises(ConfigError, match="disconnected hardware canary"):
        _apply(args, client)  # type: ignore[arg-type]
    assert client.actions == ["backup", "lag"]


def test_backup_writer_is_exclusive_and_private(tmp_path: Path) -> None:
    output = tmp_path / "private" / "switch.cfg"
    digest = _write_backup(output, b"secret backup")
    assert len(digest) == 64
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        _write_backup(output, b"replacement")
    assert output.read_bytes() == b"secret backup"


def _tagged_canary() -> dict:
    return {
        "vlan_id": "4093",
        "vlan_name": "api-canary",
        "port_states": [0, 0, 0, 0, 0, 0, 2, 0, 0, 0, 0],
    }


def test_delete_vlan_backs_up_deletes_and_verifies(tmp_path: Path, capsys) -> None:
    client = FakeClient()
    client.vlans.append(_tagged_canary())
    args = Namespace(vlan_id=4093, backup_dir=tmp_path / "backups")
    assert _delete_vlan(args, client) == 0  # type: ignore[arg-type]
    assert client.actions == ["backup", "delete:4093", "save"]
    assert [vlan["vlan_id"] for vlan in client.vlans] == ["1"]
    assert "Save requested" in capsys.readouterr().out


def test_delete_vlan_is_a_no_op_for_missing_vlan(tmp_path: Path, capsys) -> None:
    client = FakeClient()
    args = Namespace(vlan_id=4093, backup_dir=tmp_path / "backups")
    assert _delete_vlan(args, client) == 0  # type: ignore[arg-type]
    assert client.actions == []
    assert "not present" in capsys.readouterr().out


def test_delete_vlan_refuses_vlan_1(tmp_path: Path) -> None:
    client = FakeClient()
    args = Namespace(vlan_id=1, backup_dir=tmp_path / "backups")
    with pytest.raises(ConfigError, match="VLAN 1"):
        _delete_vlan(args, client)  # type: ignore[arg-type]
    assert client.actions == []


def test_delete_vlan_refuses_untagged_or_pvid_owner(tmp_path: Path) -> None:
    client = FakeClient()
    canary = _tagged_canary()
    canary["port_states"][6] = 1
    client.vlans[0]["port_states"][6] = 0
    client.vlans.append(canary)
    args = Namespace(vlan_id=4093, backup_dir=tmp_path / "backups")
    with pytest.raises(ConfigError, match="ports 6"):
        _delete_vlan(args, client)  # type: ignore[arg-type]
    assert client.actions == []


def test_delete_vlan_never_saves_when_vlan_persists(tmp_path: Path) -> None:
    client = FakeClient(converge_vlans=False)
    client.vlans.append(_tagged_canary())
    args = Namespace(vlan_id=4093, backup_dir=tmp_path / "backups")
    with pytest.raises(VerificationError, match="still present"):
        _delete_vlan(args, client)  # type: ignore[arg-type]
    assert client.actions == ["backup", "delete:4093"]
