from __future__ import annotations

import psutil
from app.services.libvirt_service import LibvirtService


def get_host_summary() -> dict:
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    cpu_percent = psutil.cpu_percent(interval=0.2)

    summary = {
        "cpu_percent": cpu_percent,
        "cpu_count": psutil.cpu_count(logical=True),
        "memory_total_mb": round(memory.total / 1024 / 1024),
        "memory_used_mb": round(memory.used / 1024 / 1024),
        "memory_percent": memory.percent,
        "disk_total_gb": round(disk.total / 1024 / 1024 / 1024, 2),
        "disk_used_gb": round(disk.used / 1024 / 1024 / 1024, 2),
        "disk_percent": disk.percent,
    }

    try:
        lv = LibvirtService()
        summary["libvirt"] = lv.node_info()
    except Exception as exc:
        summary["libvirt_error"] = str(exc)

    return summary
