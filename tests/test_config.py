from pathlib import Path

import pytest

from qsw_l2110.config import load_config
from qsw_l2110.errors import ConfigError


def test_load_config_rejects_duplicate_yaml_keys(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.yaml"
    path.write_text("schema_version: 1\nschema_version: 2\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="duplicate key 'schema_version'"):
        load_config(path)
