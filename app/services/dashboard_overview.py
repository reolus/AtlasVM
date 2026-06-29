from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any


def run(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, text=True, capture_output=True, check=check)


def split_lines(value: str) -> list[str]:
    return [line.strip() for line in (value or "").splitlines() if line.strip()]


def pct(used: float, total: float) -> float:
    if total <= 0:
        return 0.0
    return round((used / total) * 100, 1)


def human_bytes(num: float) -> str:
    units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]
    value = float(num)
    for unit in units:
        if abs(value) < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.2f} {unit}"
        value /= 1024.0
    return f"{value:.2f} PiB"


def donut_gradient(used_percent: float | None, mode: str = "normal") -> str:
    if used_percent is None:
        return "conic-gradient(var(--muted, #777) 0 100%)"

    used_percent = max(0.0, min(100.0, float(used_percent)))

    if mode == "cpu":
        return f"conic-gradient(var(--accent, #4ea1ff) 0 {used_percent}%, var(--success, #5cb85c) {used_percent}% 100%)"

    return f"conic-gradient(var(--danger, #d9534f) 0 {used_percent}%, var(--success, #5cb85c) {used_percent}% 100%)"


def cpu_usage() -> dict[str, Any]:
    def read_cpu() -> tuple[int, int]:
        parts = Path("/proc/stat").read_text().splitlines()[0].split()[1:]
        nums = [int(x) for x in parts]
        idle = nums[3] + (nums[4] if len(nums) > 4 else 0)
        total = sum(nums)
        return idle, total

    try:
        idle1, total1 = read_cpu()
        # Keep this short enough not to annoy page loads. Humans notice 1 second. They complain.
        import time
        time.sleep(0.15)
        idle2, total2 = read_cpu()

        idle_delta = idle2 - idle1
        total_delta = total2 - total1
        used = 0.0
        if total_delta > 0:
            used = round((1 - idle_delta / total_delta) * 100, 1)

        cores = os.cpu_count() or 0
        return {
            "label": "CPU",
            "used_percent": used,
            "free_percent": round(100 - used, 1),
            "used": f"{used}%",
            "available": f"{round(100 - used, 1)}% idle",
            "total": f"{cores} cores",
            "chart_gradient": donut_gradient(used, "cpu"),
        }
    except Exception as exc:
        return {
            "label": "CPU",
            "used_percent": None,
            "free_percent": None,
            "used": "Unknown",
            "available": "Unknown",
            "total": "Unknown",
            "chart_gradient": donut_gradient(None),
            "error": str(exc),
        }


def memory_usage() -> dict[str, Any]:
    try:
        values = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, value = line.split(":", 1)
            values[key] = int(value.strip().split()[0]) * 1024

        total = values.get("MemTotal", 0)
        available = values.get("MemAvailable", 0)
        used = max(0, total - available)
        used_percent = pct(used, total)

        return {
            "label": "Memory",
            "used_percent": used_percent,
            "free_percent": round(100 - used_percent, 1),
            "used": human_bytes(used),
            "available": human_bytes(available),
            "total": human_bytes(total),
            "chart_gradient": donut_gradient(used_percent),
        }
    except Exception as exc:
        return {
            "label": "Memory",
            "used_percent": None,
            "free_percent": None,
            "used": "Unknown",
            "available": "Unknown",
            "total": "Unknown",
            "chart_gradient": donut_gradient(None),
            "error": str(exc),
        }


def root_disk_usage() -> dict[str, Any]:
    try:
        st = os.statvfs("/")
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        used = max(0, total - free)
        used_percent = pct(used, total)

        return {
            "label": "Root Disk",
            "used_percent": used_percent,
            "free_percent": round(100 - used_percent, 1),
            "used": human_bytes(used),
            "available": human_bytes(free),
            "total": human_bytes(total),
            "chart_gradient": donut_gradient(used_percent),
        }
    except Exception as exc:
        return {
            "label": "Root Disk",
            "used_percent": None,
            "free_percent": None,
            "used": "Unknown",
            "available": "Unknown",
            "total": "Unknown",
            "chart_gradient": donut_gradient(None),
            "error": str(exc),
        }


def vm_summary() -> dict[str, Any]:
    result = run(["virsh", "list", "--all"])
    total = 0
    running = 0
    offline = 0

    if result.returncode != 0:
        return {"total": 0, "running": 0, "offline": 0, "error": result.stderr}

    for line in split_lines(result.stdout):
        stripped = line.strip()
        if stripped.startswith("Id") or stripped.startswith("-"):
            continue

        parts = stripped.split(None, 2)
        if len(parts) < 3:
            continue

        total += 1
        state = parts[2].lower()

        if "running" in state:
            running += 1
        else:
            offline += 1

    return {
        "total": total,
        "running": running,
        "offline": offline,
    }


def libvirt_pool_health() -> list[dict[str, Any]]:
    try:
        from app.services.storage_phase9 import storage_overview

        pools = storage_overview().get("libvirt_pools", [])
    except Exception as exc:
        return [{"name": "error", "state": "error", "note": str(exc)}]

    out = []
    for pool in pools:
        name = pool.get("name")
        if not name or name == "Name":
            continue

        mode = pool.get("usage_mode", "")
        state = pool.get("state", "")
        autostart = pool.get("autostart", "")
        note = pool.get("chart_note", "")

        if state == "active":
            health = "healthy"
        else:
            health = "inactive"

        if mode == "block-presented":
            label_used = "Presented"
            label_free = "Filesystem free not available"
        elif mode == "lvm-thin":
            thin = pool.get("lvm_thin") or {}
            label_used = f"{thin.get('data_percent', 0)}% data used"
            label_free = f"{thin.get('free_percent', 100)}% data free"
        else:
            label_used = pool.get("allocation", "")
            label_free = pool.get("available", "")

        out.append({
            "name": name,
            "type": pool.get("type", ""),
            "mode": mode,
            "state": state,
            "autostart": autostart,
            "health": health,
            "path": pool.get("path", ""),
            "used_percent": pool.get("used_percent"),
            "free_percent": pool.get("free_percent"),
            "capacity": pool.get("capacity", ""),
            "used": label_used,
            "available": label_free,
            "note": note,
            "lvm_thin": pool.get("lvm_thin"),
        })

    return out


def iscsi_overview() -> list[dict[str, Any]]:
    try:
        from app.services.storage_phase9 import (
            list_iscsi_targets,
            list_iscsi_sessions,
            list_lvm_storage_summary,
        )
    except Exception as exc:
        return [{"name": "error", "connected": False, "error": str(exc)}]

    targets = list_iscsi_targets()
    sessions = list_iscsi_sessions()
    lvm = list_lvm_storage_summary()

    vgs = lvm.get("vgs", [])
    lvs = lvm.get("lvs", [])

    out = []

    for name, target in targets.items():
        iqn = target.get("target_iqn", "")
        portal = target.get("portal", "")

        connected = False
        for session in sessions:
            if session.get("target_iqn") == iqn:
                connected = True
                break

        associated = []
        lvm_thin = target.get("lvm_thin") or {}
        vg_name = lvm_thin.get("vg_name", "")

        if vg_name:
            vg_rows = [vg for vg in vgs if vg.get("vg_name") == vg_name]
            lv_rows = [lv for lv in lvs if lv.get("vg_name") == vg_name]
            associated.append({
                "vg_name": vg_name,
                "thinpool_name": lvm_thin.get("thinpool_name", ""),
                "vg": vg_rows,
                "lvs": lv_rows,
            })

        out.append({
            "name": name,
            "portal": portal,
            "target_iqn": iqn,
            "connected": connected,
            "status": "connected" if connected else "disconnected",
            "lvm": associated,
        })

    return out


def dashboard_overview() -> dict[str, Any]:
    return {
        "host": {
            "cpu": cpu_usage(),
            "memory": memory_usage(),
            "root_disk": root_disk_usage(),
        },
        "vms": vm_summary(),
        "libvirt_pools": libvirt_pool_health(),
        "iscsi": iscsi_overview(),
    }
