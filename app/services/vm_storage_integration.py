from __future__ import annotations

import html
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


def run(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, text=True, capture_output=True, check=check)


def validate_volume_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise RuntimeError("Volume name is required.")

    if not re.match(r"^[A-Za-z0-9_.-]+$", name):
        raise RuntimeError("Volume name may only contain letters, numbers, dots, underscores, and hyphens.")

    return name


def pool_xml(pool_name: str) -> ET.Element:
    result = run(["virsh", "pool-dumpxml", pool_name], check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or f"Could not inspect storage pool {pool_name}.")
    return ET.fromstring(result.stdout)


def pool_type(pool_name: str) -> str:
    return pool_xml(pool_name).get("type", "")


def ensure_pool_active(pool_name: str) -> None:
    info = run(["virsh", "pool-info", pool_name], check=False)
    if info.returncode != 0:
        raise RuntimeError(info.stderr or f"Storage pool not found: {pool_name}")

    if "State:          running" not in info.stdout and "State: running" not in info.stdout:
        start = run(["virsh", "pool-start", pool_name], check=False)
        if start.returncode != 0:
            raise RuntimeError(start.stderr or start.stdout or f"Could not start storage pool {pool_name}.")

    run(["virsh", "pool-refresh", pool_name], check=False)


def create_vm_disk_volume(
    pool_name: str,
    vm_name: str,
    size_gb: int,
    disk_index: int = 1,
) -> dict[str, Any]:
    pool_name = validate_volume_name(pool_name)
    vm_name = validate_volume_name(vm_name)

    if size_gb < 1:
        raise RuntimeError("Disk size must be at least 1 GB.")

    ensure_pool_active(pool_name)

    ptype = pool_type(pool_name)

    if ptype == "logical":
        volume_name = f"{vm_name}-disk{disk_index}"
        volume_name = validate_volume_name(volume_name)

        create = run(
            ["virsh", "vol-create-as", "--pool", pool_name, "--name", volume_name, "--capacity", f"{size_gb}G"],
            check=False,
        )
        if create.returncode != 0:
            raise RuntimeError(create.stderr or create.stdout or "Failed to create LVM logical volume.")

        path = run(["virsh", "vol-path", "--pool", pool_name, volume_name], check=False)
        if path.returncode != 0:
            raise RuntimeError(path.stderr or "Logical volume was created, but libvirt could not resolve its path.")

        run(["virsh", "pool-refresh", pool_name], check=False)

        return {
            "pool": pool_name,
            "pool_type": ptype,
            "volume": volume_name,
            "path": path.stdout.strip(),
            "disk_type": "block",
            "source_attr": "dev",
            "driver_type": "raw",
            "format": "raw",
        }

    if ptype in {"dir", "fs", "netfs"}:
        volume_name = f"{vm_name}-disk{disk_index}.qcow2"
        volume_name = validate_volume_name(volume_name)

        create = run(
            [
                "virsh", "vol-create-as",
                "--pool", pool_name,
                "--name", volume_name,
                "--capacity", f"{size_gb}G",
                "--format", "qcow2",
            ],
            check=False,
        )
        if create.returncode != 0:
            raise RuntimeError(create.stderr or create.stdout or "Failed to create qcow2 disk volume.")

        path = run(["virsh", "vol-path", "--pool", pool_name, volume_name], check=False)
        if path.returncode != 0:
            raise RuntimeError(path.stderr or "Disk volume was created, but libvirt could not resolve its path.")

        run(["virsh", "pool-refresh", pool_name], check=False)

        return {
            "pool": pool_name,
            "pool_type": ptype,
            "volume": volume_name,
            "path": path.stdout.strip(),
            "disk_type": "file",
            "source_attr": "file",
            "driver_type": "qcow2",
            "format": "qcow2",
        }

    raise RuntimeError(f"VM disk creation is not supported for storage pool type: {ptype}")


def build_vm_disk_xml(disk: dict[str, Any], target_dev: str = "vda") -> str:
    disk_type = disk.get("disk_type") or "file"
    source_attr = disk.get("source_attr") or ("dev" if disk_type == "block" else "file")
    source_path = disk.get("path") or ""
    driver_type = disk.get("driver_type") or ("raw" if disk_type == "block" else "qcow2")

    if not source_path:
        raise RuntimeError("Disk source path is missing.")

    return f"""
            <disk type='{html.escape(disk_type)}' device='disk'>
              <driver name='qemu' type='{html.escape(driver_type)}' discard='unmap'/>
              <source {html.escape(source_attr)}='{html.escape(source_path)}'/>
              <target dev='{html.escape(target_dev)}' bus='virtio'/>
            </disk>
    """


def disk_sources_from_domain_xml(xml_text: str) -> list[str]:
    root = ET.fromstring(xml_text)
    sources: list[str] = []

    for disk in root.findall("./devices/disk"):
        if disk.get("device") != "disk":
            continue

        source = disk.find("source")
        if source is None:
            continue

        value = source.get("file") or source.get("dev") or source.get("name")
        if value:
            sources.append(value)

    return sources


def find_libvirt_volume_by_path(source_path: str) -> dict[str, str] | None:
    pools = run(["virsh", "pool-list", "--all", "--name"], check=False)
    if pools.returncode != 0:
        return None

    for pool in [line.strip() for line in pools.stdout.splitlines() if line.strip()]:
        vols = run(["virsh", "vol-list", "--pool", pool, "--name"], check=False)
        if vols.returncode != 0:
            continue

        for volume in [line.strip() for line in vols.stdout.splitlines() if line.strip()]:
            path = run(["virsh", "vol-path", "--pool", pool, volume], check=False)
            if path.returncode == 0 and path.stdout.strip() == source_path:
                return {"pool": pool, "volume": volume}

    return None


def delete_disk_source(source_path: str) -> dict[str, Any]:
    source_path = (source_path or "").strip()
    if not source_path:
        return {"deleted": False, "message": "No disk source path provided."}

    match = find_libvirt_volume_by_path(source_path)
    if match:
        result = run(["virsh", "vol-delete", "--pool", match["pool"], match["volume"]], check=False)
        if result.returncode != 0:
            raise RuntimeError(result.stderr or result.stdout or f"Failed to delete volume {match['volume']}.")
        return {
            "deleted": True,
            "message": f"Deleted libvirt volume {match['volume']} from pool {match['pool']}.",
        }

    if source_path.startswith("/dev/"):
        if not (source_path.startswith("/dev/mapper/") or len(Path(source_path).parts) >= 3):
            raise RuntimeError(f"Refusing to remove suspicious block device path: {source_path}")

        result = run(["lvremove", "-y", source_path], check=False)
        if result.returncode != 0:
            raise RuntimeError(result.stderr or result.stdout or f"Failed to remove logical volume {source_path}.")

        return {
            "deleted": True,
            "message": f"Removed logical volume {source_path}.",
        }

    file_path = Path(source_path)

    allowed_prefixes = [
        "/atlasvm-vmdata/",
        "/atlasvm-storage/",
        "/var/lib/libvirt/images/",
    ]

    if not any(str(file_path).startswith(prefix) for prefix in allowed_prefixes):
        raise RuntimeError(f"Refusing to delete disk file outside known VM storage paths: {source_path}")

    if file_path.exists():
        file_path.unlink()
        return {"deleted": True, "message": f"Deleted disk file {source_path}."}

    return {"deleted": False, "message": f"Disk file did not exist: {source_path}"}
