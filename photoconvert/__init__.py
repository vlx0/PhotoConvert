"""PhotoConvert, day 89 of 365."""

from pathlib import Path
import sys

vendor = Path(__file__).resolve().parents[1] / '_vendor'
if vendor.is_dir():
    sys.path.insert(0, str(vendor))
