from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.database import init_db

if __name__ == "__main__":
    init_db()
    print("AtlasVM database initialized.")
