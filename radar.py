#!/usr/bin/env python3
"""Project entry point; the installable skill contains the implementation."""
import runpy
import sys
from pathlib import Path

scripts = Path(__file__).resolve().parent / "skills" / "mcd-expiry-radar" / "scripts"
sys.path.insert(0, str(scripts))
runpy.run_path(str(scripts / "radar.py"), run_name="__main__")
