import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fleet_data import ensure_dbs  # noqa: E402

ensure_dbs()
