from __future__ import annotations

import os
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


def run(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, text=True, capture_output=True, check=check)


def split_lines(value: str) -> list[str]:
    return [line.strip() for line in (value or "").splitlines() if line.strip()]


def validate_vm_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise RuntimeError("VM name is required.")
    return name


def validate_simple_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise RuntimeError("Name is required.")

    if not re.match(r"^[A-Za-z0-9_.-]+$", name):
        raise RuntimeError("Name may only contain letters, numbers, dots, underscores, and hyphens.")

    return name


def domstate(vm_name: str) -> str:
    result = run(["virsh", "domstate", vm_name], check=False)
    if result.returncode != 0:
        return "unknown"
    return result.stdout.strip().lower()


def is_vm_running(vm_name: str) -> bool:
    return "running" in domstate(vm_name)


def dumpxml(vm_name: str) -> str:
    result = run(["virsh", "dumpxml", vm_name], check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or f"Could not read VM XML for {vm_name}.")
    return result.stdout


def get_vm_disks(vm_name: str) -> list[dict[str, Any]]:
    xml_text = dumpxml(vm_name)
    root = ET.fromstring(xml_text)
    disks = []

    devices = root.find("devices")
    if devices is None:
        return disks

    for disk in devices.findall("disk"):
        device = disk.get("device", "")
        if device not in {"disk", "cdrom"}:
            continue

        target = disk.find("target")
        source = disk.find("source")
        driver = disk.find("driver")

        source_value = ""
        source_attr = ""

        if source is not None:
            for attr in ["file", "dev", "name", "volume", "protocol"]:
                if source.get(attr):
                    source_value = source.get(attr) or ""
                    source_attr = attr
                    break

        disks.append({
            "device": device,
            "type": disk.get("type", ""),
            "target": target.get("dev", "") if target is not None else "",
            "bus": target.get("bus", "") if target is not None else "",
            "driver": driver.get("name", "") if driver is not None else "",
            "format": driver.get("type", "") if driver is not None else "",
            "source": source_value,
            "source_attr": source_attr,
            "readonly": disk.find("readonly") is not None,
        })

    return disks


def next_virtio_target(vm_name: str) -> str:
    used = set()
    for disk in get_vm_disks(vm_name):
        target = disk.get("target", "")
        if target.startswith("vd"):
            used.add(target)

    for letter in "bcdefghijklmnopqrstuvwxyz":
        candidate = f"vd{letter}"
        if candidate not in used:
            return candidate

    raise RuntimeError("No available virtio disk target names remain.")


def list_storage_pools_for_disks() -> list[dict[str, Any]]:
    try:
        from app.services.storage_phase9 import storage_overview
        pools = storage_overview().get("libvirt_pools", [])
    except Exception:
        pools = []

    out = []
    for p in pools:
        name = p.get("name", "")
        if not name or name == "Name":
            continue

        pool_type = p.get("type", "")
        mode = p.get("usage_mode", "")

        # These are the pool types we support for add-disk now.
        supported = pool_type in {"dir", "logical"}

        out.append({
            "name": name,
            "type": pool_type,
            "mode": mode,
            "state": p.get("state", ""),
            "capacity": p.get("capacity", ""),
            "available": p.get("available", ""),
            "path": p.get("path", ""),
            "supported": supported,
            "note": "" if supported else "Add-disk not supported for this pool type yet.",
        })

    return out


def pool_type(pool_name: str) -> str:
    result = run(["virsh", "pool-dumpxml", pool_name], check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or f"Could not inspect pool {pool_name}.")

    root = ET.fromstring(result.stdout)
    return root.get("type", "")


def pool_target_path(pool_name: str) -> str:
    result = run(["virsh", "pool-dumpxml", pool_name], check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or f"Could not inspect pool {pool_name}.")

    root = ET.fromstring(result.stdout)
    return root.findtext("./target/path") or ""


def create_volume(pool_name: str, vol_name: str, size_gb: int, fmt: str) -> dict[str, Any]:
    pool_name = validate_simple_name(pool_name)
    vol_name = validate_simple_name(vol_name)

    if size_gb <= 0:
        raise RuntimeError("Disk size must be greater than zero.")

    ptype = pool_type(pool_name)

    if ptype == "dir":
        if not vol_name.endswith(".qcow2") and fmt == "qcow2":
            vol_name = f"{vol_name}.qcow2"

        result = run([
            "virsh", "vol-create-as",
            pool_name,
            vol_name,
            f"{size_gb}G",
            "--format", fmt,
        ], check=False)

    elif ptype == "logical":
        # Logical pools create LVs. Do not pass qcow2 here. The LV is block storage.
        result = run([
            "virsh", "vol-create-as",
            pool_name,
            vol_name,
            f"{size_gb}G",
        ], check=False)

    else:
        raise RuntimeError(f"Add disk is not supported for pool type {ptype} yet.")

    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or "Volume creation failed.")

    path_result = run(["virsh", "vol-path", "--pool", pool_name, vol_name], check=False)
    if path_result.returncode != 0:
        raise RuntimeError(path_result.stderr or "Volume was created but path could not be resolved.")

    return {
        "pool": pool_name,
        "name": vol_name,
        "path": path_result.stdout.strip(),
        "type": ptype,
        "format": fmt if ptype == "dir" else "raw",
        "size_gb": size_gb,
    }


def attach_disk(vm_name: str, volume: dict[str, Any], target_dev: str | None = None) -> dict[str, Any]:
    vm_name = validate_vm_name(vm_name)
    target_dev = target_dev or next_virtio_target(vm_name)

    disk_path = volume["path"]
    ptype = volume["type"]
    fmt = volume.get("format") or "raw"

    cmd = [
        "virsh", "attach-disk",
        vm_name,
        disk_path,
        target_dev,
        "--targetbus", "virtio",
    ]

    if ptype == "dir":
        cmd.extend(["--driver", "qemu", "--subdriver", fmt])
    else:
        cmd.extend(["--driver", "qemu", "--subdriver", "raw"])

    if is_vm_running(vm_name):
        cmd.extend(["--live", "--config"])
        attach_mode = "live+config"
    else:
        cmd.append("--config")
        attach_mode = "config"

    result = run(cmd, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or "Disk attach failed.")

    return {
        "vm": vm_name,
        "target": target_dev,
        "path": disk_path,
        "attach_mode": attach_mode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def add_disk_to_vm(
    vm_name: str,
    pool_name: str,
    disk_name: str,
    size_gb: int,
    fmt: str = "qcow2",
) -> dict[str, Any]:
    vm_name = validate_vm_name(vm_name)
    pool_name = validate_simple_name(pool_name)
    disk_name = validate_simple_name(disk_name)

    if fmt not in {"qcow2", "raw"}:
        raise RuntimeError("Disk format must be qcow2 or raw.")

    ptype = pool_type(pool_name)
    if ptype == "logical":
        fmt = "raw"

    volume = create_volume(pool_name, disk_name, size_gb, fmt)
    attach = attach_disk(vm_name, volume)

    return {
        "volume": volume,
        "attach": attach,
    }


def detach_disk_from_vm(vm_name: str, target_dev: str) -> dict[str, Any]:
    vm_name = validate_vm_name(vm_name)
    target_dev = validate_simple_name(target_dev)

    cmd = ["virsh", "detach-disk", vm_name, target_dev]

    if is_vm_running(vm_name):
        cmd.extend(["--live", "--config"])
        detach_mode = "live+config"
    else:
        cmd.append("--config")
        detach_mode = "config"

    result = run(cmd, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or "Disk detach failed.")

    return {
        "vm": vm_name,
        "target": target_dev,
        "detach_mode": detach_mode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def find_disk(vm_name: str, target_dev: str) -> dict[str, Any]:
    for disk in get_vm_disks(vm_name):
        if disk.get("target") == target_dev:
            return disk
    raise RuntimeError(f"Disk target {target_dev} was not found on VM {vm_name}.")


def delete_backing_storage(disk: dict[str, Any]) -> dict[str, Any]:
    source = disk.get("source", "")
    dtype = disk.get("type", "")

    if not source:
        return {"deleted": False, "message": "Disk has no source path to delete."}

    if disk.get("device") != "disk":
        return {"deleted": False, "message": "Refusing to delete non-disk device."}

    # File-backed qcow2/raw image.
    if dtype == "file":
        path = Path(source)
        if not path.exists():
            return {"deleted": False, "message": f"Backing file does not exist: {source}"}

        # Safety rails. Do not delete random host paths.
        allowed_prefixes = [
            "/atlasvm-vmdata/",
            "/atlasvm-storage/",
            "/var/lib/libvirt/images/",
        ]

        if not any(str(path).startswith(prefix) for prefix in allowed_prefixes):
            raise RuntimeError(f"Refusing to delete backing file outside known storage paths: {source}")

        path.unlink()
        return {"deleted": True, "message": f"Deleted backing file {source}"}

    # Block-backed LV.
    if dtype == "block":
        if not source.startswith("/dev/"):
            raise RuntimeError(f"Refusing to delete non-/dev block source: {source}")

        # Try to remove as a libvirt volume first by matching vol path.
        pool_match = find_pool_volume_by_path(source)
        if pool_match:
            result = run(["virsh", "vol-delete", "--pool", pool_match["pool"], pool_match["volume"]], check=False)
            if result.returncode != 0:
                raise RuntimeError(result.stderr or result.stdout or "Failed to delete libvirt volume.")
            return {"deleted": True, "message": f"Deleted volume {pool_match['volume']} from pool {pool_match['pool']}"}

        # Fallback for LVM LV path.
        if source.startswith("/dev/mapper/") or len(Path(source).parts) >= 3:
            result = run(["lvremove", "-y", source], check=False)
            if result.returncode != 0:
                raise RuntimeError(result.stderr or result.stdout or "Failed to remove logical volume.")
            return {"deleted": True, "message": f"Removed logical volume {source}"}

    return {"deleted": False, "message": f"Deletion not implemented for disk type {dtype} source {source}"}


def find_pool_volume_by_path(path: str) -> dict[str, str] | None:
    pools_result = run(["virsh", "pool-list", "--all", "--name"], check=False)
    if pools_result.returncode != 0:
        return None

    for pool in split_lines(pools_result.stdout):
        vols_result = run(["virsh", "vol-list", "--pool", pool, "--details"], check=False)
        if vols_result.returncode != 0:
            continue

        for line in split_lines(vols_result.stdout):
            if line.startswith("Name") or line.startswith("-"):
                continue

            parts = line.split()
            if not parts:
                continue

            vol_name = parts[0]
            path_result = run(["virsh", "vol-path", "--pool", pool, vol_name], check=False)
            if path_result.returncode == 0 and path_result.stdout.strip() == path:
                return {"pool": pool, "volume": vol_name}

    return None


def remove_disk_from_vm(vm_name: str, target_dev: str, delete_storage: bool = False) -> dict[str, Any]:
    vm_name = validate_vm_name(vm_name)
    target_dev = validate_simple_name(target_dev)

    disk = find_disk(vm_name, target_dev)

    if disk.get("device") != "disk":
        raise RuntimeError("Only disk devices can be removed here. CD-ROMs are not handled by this action.")

    if target_dev in {"vda", "sda", "hda"} and delete_storage:
        raise RuntimeError("Refusing to delete likely boot disk backing storage. Detach without delete first if you really mean it.")

    detach = detach_disk_from_vm(vm_name, target_dev)

    delete_result = {"deleted": False, "message": "Storage was not deleted."}
    if delete_storage:
        delete_result = delete_backing_storage(disk)

    return {
        "disk": disk,
        "detach": detach,
        "delete": delete_result,
    }
