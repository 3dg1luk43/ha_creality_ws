#!/usr/bin/env python3
"""Creality printer simulator: the old entry point.

The simulator now lives in tools/simulator/ (a package: profiles, the printer
state machine, frames, protocol, servers, cameras, and a control UI on the
control port at /ui). This file keeps the old command line and the names
scripts and tests import from it.

    python3 tools/creality_printer_test_server.py --model k1c --simulate-print
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from simulator.cli import build_argparser, main  # noqa: E402,F401
from simulator.printer import (  # noqa: E402,F401
    MATERIAL_WRITABLE_KEYS,
    MODEL_CONFIGS,
    PrinterState,
    SimOptions,
    _M106_RE,
)
from simulator.profiles import PROFILES  # noqa: E402,F401

if __name__ == "__main__":
    main()
