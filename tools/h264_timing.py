"""Moved to tools/simulator/h264_timing.py; kept so old imports keep working."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from simulator.h264_timing import *  # noqa: E402,F401,F403
from simulator.h264_timing import assign_clip_timestamps  # noqa: E402,F401
