"""Puts src/ on the import path for `python -m unittest discover -s tests -t .`."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'src'
REFERENCE = ROOT / 'reference'
sys.path.insert(0, str(SRC))
