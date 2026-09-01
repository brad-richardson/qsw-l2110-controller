from __future__ import annotations

from argparse import Namespace
from pathlib import Path

import pytest
import yaml
from test_models import desired_mapping
from test_reconcile import current_default_vlan, current_lags

from qsw_l2110.cli import _apply
from qsw_l2110.errors import VerificationError


class FakeClient:
    def __init__(self, *, converge_vlans: bool = True) -> None:
        self.lags = current_lags()
        self.vlans = current_default_vlan()
        self.converge_vlans = converge_vlans
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

    def download_backup(self) -> bytes:
        self.actions.append("backup")
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

    def set_vlans(self, payload: dict) -> None:
        self.actions.append("vlan")
        if self.converge_vlans:
            self.vlans = payload["updatedVlans"]

    def save(self) -> None:
        self.actions.append("save")


def write_config(path: Path) -> None:
    path.write_text(yaml.safe_dump(desired_mapping(), sort_keys=False), encoding="utf-8")


def test_apply_backs_up_orders_writes_and_verifies(tmp_path: Path) -> None:
    config_path = tmp_path / "switch.yaml"
    write_config(config_path)
    client = FakeClient()
    args = Namespace(config=config_path, backup_dir=tmp_path / "backups")
    assert _apply(args, client) == 0  # type: ignore[arg-type]
    assert client.actions == ["backup", "lag", "vlan", "save"]
    assert len(list((tmp_path / "backups").glob("*.cfg"))) == 1


def test_apply_never_saves_failed_vlan_readback(tmp_path: Path) -> None:
    config_path = tmp_path / "switch.yaml"
    write_config(config_path)
    client = FakeClient(converge_vlans=False)
    args = Namespace(config=config_path, backup_dir=tmp_path / "backups")
    with pytest.raises(VerificationError, match="read-back differs"):
        _apply(args, client)  # type: ignore[arg-type]
    assert client.actions == ["backup", "lag", "vlan"]
