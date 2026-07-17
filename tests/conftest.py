"""Pytest configuration for stratum-race tests."""

import sys
from pathlib import Path

# Add project root to path so lib/ is importable
_root_path = str(Path(__file__).parent.parent)
if _root_path not in sys.path:
    sys.path.insert(0, _root_path)

# Add collector/ to import path so collector tests can import str_race directly
_collector_path = str(Path(__file__).parent.parent / "collector")
if _collector_path not in sys.path:
    sys.path.insert(0, _collector_path)
