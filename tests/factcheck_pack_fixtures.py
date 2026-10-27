"""Shared module loading for the ``factcheck_pack`` unit-test modules.

The script is loaded here once by path so every test module patches the same
``fc`` module object. This file intentionally contains no test methods.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "factcheck_pack.py"

_spec = importlib.util.spec_from_file_location("factcheck_pack", SCRIPT_PATH)
fc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fc)
