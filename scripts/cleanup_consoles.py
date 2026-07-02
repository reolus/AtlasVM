#!/usr/bin/env python3

import json
import os
import signal
from pathlib import Path

STATE_PATH = Path("/run/atlasvm/consoles.json")


def pid_exists(pid: int) -> bool:
    return Path(f"/proc/{pid}").exists()


def pid_is_websockify(pid: int) -> bool:
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_text(errors="ignore")
        return "websockify" in cmdline or "novnc" in cmdline
    except Exception:
        return False


def main() -> None:
    if not STATE_PATH.exists():
        return

    try:
        state = json.loads(STATE_PATH.read_text())
    except Exception:
        STATE_PATH.unlink(missing_ok=True)
        return

    changed = False

    for vm_name, item in list(state.items()):
        pid = item.get("pid")
        if not pid:
            state.pop(vm_name, None)
            changed = True
            continue

        pid = int(pid)

        if not pid_exists(pid):
            state.pop(vm_name, None)
            changed = True
            continue

        if not pid_is_websockify(pid):
            state.pop(vm_name, None)
            changed = True
            continue

    if changed:
        STATE_PATH.write_text(json.dumps(state, indent=2))


if __name__ == "__main__":
    main()