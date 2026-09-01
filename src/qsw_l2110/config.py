from pathlib import Path

import yaml

from qsw_l2110.errors import ConfigError
from qsw_l2110.models import DesiredConfig


def load_config(path: str | Path) -> DesiredConfig:
    config_path = Path(path)
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"cannot read configuration {config_path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {config_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("configuration root must be a mapping")
    return DesiredConfig.from_mapping(raw)
