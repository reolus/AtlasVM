from __future__ import annotations

import re
from pathlib import Path

APP_ROOT = Path('/opt/atlasvm')
PACKAGE_ROOT = Path(__file__).resolve().parents[2]


def atlasvm_version() -> str:
    candidates = [
        APP_ROOT / 'VERSION',
        PACKAGE_ROOT / 'VERSION',
    ]

    for path in candidates:
        try:
            if path.exists():
                value = path.read_text(errors='ignore').strip().splitlines()[0].strip()
                if value:
                    return value
        except Exception:
            pass

    readme_candidates = [APP_ROOT / 'README.md', PACKAGE_ROOT / 'README.md']
    for readme in readme_candidates:
        try:
            if not readme.exists():
                continue
            for line in readme.read_text(errors='ignore').splitlines()[:50]:
                clean = line.strip().lstrip('#').strip()
                if not clean:
                    continue
                if clean.startswith('<'):
                    continue
                if 'AtlasVM' in clean and len(clean) < 120:
                    clean = re.sub(r'<[^>]+>', '', clean).strip()
                    if clean:
                        return clean
        except Exception:
            pass

    return 'AtlasVM development build'
