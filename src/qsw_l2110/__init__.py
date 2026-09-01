"""Declarative configuration models for QNAP QSW-L2110 switches.

Safe write orchestration is exposed through the CLI. The private-API transport
remains available explicitly from :mod:`qsw_l2110.client` for protocol work.
"""

from qsw_l2110.config import load_config
from qsw_l2110.models import DesiredConfig

__all__ = ["DesiredConfig", "load_config"]
__version__ = "0.1.0a0"
