import sys
from pathlib import Path

# Allow `python -m evaluation.run` from the repo root without installing the backend package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
