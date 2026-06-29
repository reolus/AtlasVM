from __future__ import annotations

import json
import os
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


STORAGE_NETWORKS_FILE = Path("/opt/atlasvm/atlasvm_storage_networks.json")
NETWORKD_DIR = Path("/etc/systemd/network")


def run(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, text=True, capture_output=True, check=check)


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True))


def _split_lines(output: str) -> list[str]:
    return [line.rstrip() for line in output.splitlines() if line.strip()]


def storage_overview() -> dict[str, Any]:
    return {
        "zfs_pools": list_zfs_pools(),
        "zfs_status": zfs_status_text(),
        "zfs_datasets": list_zfs_datasets(),
        "libvirt_pools": list_libvirt_pools(),
        "filesystems": list_filesystems(),
        "vm_disks": list_vm_disks(),
        "storage_networks": list_storage_networks(),
        "nfs_targets": list_nfs_targets(),
        "smb_targets": list_smb_targets(),
        "iscsi_targets": list_iscsi_targets(),
        "iscsi_sessions": list_iscsi_sessions(),
        "iscsi_block_devices": list_iscsi_block_devices(),
        "iscsi_device_details": list_iscsi_device_details(),
        "lvm_summary": list_lvm_storage_summary(),
        "host_links": list_host_links(),
        "default_route": default_route(),
    }


def list_zfs_pools() -> list[dict[str, str]]:
    # Human-readable view plus byte-accurate values for charts.
    human = run(["zpool", "list", "-H", "-o", "name,size,alloc,free,cap,health"])
    raw = run(["zpool", "list", "-Hp", "-o", "name,size,alloc,free,cap,health"])

    human_rows = {}
    if human.returncode == 0:
        for line in _split_lines(human.stdout):
            parts = line.split()
            if len(parts) >= 6:
                human_rows[parts[0]] = {
                    "name": parts[0],
                    "size": parts[1],
                    "alloc": parts[2],
                    "free": parts[3],
                    "cap": parts[4],
                    "health": parts[5],
                }

    pools = []
    if raw.returncode != 0:
        return list(human_rows.values())

    for line in _split_lines(raw.stdout):
        parts = line.split()
        if len(parts) < 6:
            continue

        name = parts[0]
        size_bytes = int(parts[1])
        alloc_bytes = int(parts[2])
        free_bytes = int(parts[3])

        used_percent = 0
        free_percent = 0
        if size_bytes > 0:
            used_percent = round((alloc_bytes / size_bytes) * 100, 1)
            free_percent = round((free_bytes / size_bytes) * 100, 1)

        row = human_rows.get(name, {"name": name})
        row.update({
            "size_bytes": size_bytes,
            "alloc_bytes": alloc_bytes,
            "free_bytes": free_bytes,
            "used_percent": used_percent,
            "free_percent": free_percent,
            "chart_gradient": f"conic-gradient(var(--danger, #d9534f) 0 {used_percent}%, var(--success, #5cb85c) {used_percent}% 100%)",
        })
        pools.append(row)

    return pools



def zfs_status_text() -> str:
    result = run(["zpool", "status"])
    return result.stdout if result.returncode == 0 else result.stderr


def list_zfs_datasets() -> list[dict[str, str]]:
    result = run(["zfs", "list", "-H", "-o", "name,type,used,avail,refer,mountpoint"])
    datasets = []
    if result.returncode != 0:
        return datasets

    for line in _split_lines(result.stdout):
        parts = line.split(None, 5)
        if len(parts) == 6:
            datasets.append({
                "name": parts[0],
                "type": parts[1],
                "used": parts[2],
                "avail": parts[3],
                "refer": parts[4],
                "mountpoint": parts[5],
            })
    return datasets


def _pool_target_path(xml_text: str) -> str:
    try:
        root = ET.fromstring(xml_text)
        target = root.find("target")
        if target is not None:
            path = target.findtext("path")
            return path or ""
    except Exception:
        pass
    return ""


def _pool_capacity_bytes(xml_text: str) -> dict[str, int]:
    out = {"capacity_bytes": 0, "allocation_bytes": 0, "available_bytes": 0}
    try:
        root = ET.fromstring(xml_text)
        for key, tag in [
            ("capacity_bytes", "capacity"),
            ("allocation_bytes", "allocation"),
            ("available_bytes", "available"),
        ]:
            value = root.findtext(tag)
            if value:
                out[key] = int(value)
    except Exception:
        pass
    return out


def _pool_type(xml_text: str) -> str:
    try:
        root = ET.fromstring(xml_text)
        return root.attrib.get("type", "")
    except Exception:
        return ""


def _pool_source_name(xml_text: str) -> str:
    try:
        root = ET.fromstring(xml_text)
        source = root.find("source")
        if source is not None:
            name = source.findtext("name")
            return name or ""
    except Exception:
        pass
    return ""


def _lvm_thin_usage_for_vg(vg_name: str) -> dict[str, Any]:
    """
    Return thin-pool usage for a VG.

    For LVM-thin, libvirt pool allocation often reflects the thinpool LV size,
    not actual data consumed. The real useful numbers are lvs data_percent and
    metadata_percent. Naturally, those live somewhere else, because storage.
    """
    if not vg_name:
        return {}

    result = run([
        "lvs",
        "--readonly",
        "--reportformat", "json",
        "--units", "g",
        "-a",
        "-o", "lv_name,vg_name,lv_size,lv_attr,pool_lv,data_percent,metadata_percent",
        vg_name,
    ], check=False)

    if result.returncode != 0:
        return {}

    try:
        data = json.loads(result.stdout)
    except Exception:
        return {}

    candidates = []

    for report in data.get("report", []):
        for lv in report.get("lv", []):
            if lv.get("vg_name") != vg_name:
                continue

            lv_attr = str(lv.get("lv_attr") or "")
            data_percent = str(lv.get("data_percent") or "").strip()
            metadata_percent = str(lv.get("metadata_percent") or "").strip()

            # Thin pools usually expose data_percent and metadata_percent.
            if data_percent or metadata_percent or "t" in lv_attr.lower():
                candidates.append(lv)

    if not candidates:
        return {}

    # Prefer the LV with data_percent populated.
    selected = None
    for lv in candidates:
        if str(lv.get("data_percent") or "").strip():
            selected = lv
            break

    if selected is None:
        selected = candidates[0]

    def pct(value: Any, default: float | None = None) -> float | None:
        value = str(value or "").strip()
        if not value:
            return default
        try:
            return round(float(value), 1)
        except Exception:
            return default

    # LVM sometimes returns blank data_percent/metadata_percent for a newly
    # created thin pool with no thin volumes yet. Treat that as 0%, not unknown.
    # Thank you, LVM, for making "nothing used" look like "who knows."
    data_pct = pct(selected.get("data_percent"), 0.0)
    meta_pct = pct(selected.get("metadata_percent"), 0.0)

    free_pct = None
    if data_pct is not None:
        free_pct = round(100 - data_pct, 1)

    return {
        "thinpool_name": selected.get("lv_name", ""),
        "thinpool_size": selected.get("lv_size", ""),
        "data_percent": data_pct,
        "free_percent": free_pct,
        "metadata_percent": meta_pct,
        "lv_attr": selected.get("lv_attr", ""),
        "raw_data_percent": selected.get("data_percent", ""),
        "raw_metadata_percent": selected.get("metadata_percent", ""),
    }


def _pool_usage_mode(pool_type: str) -> str:
    pool_type = (pool_type or "").strip().lower()
    if pool_type in {"iscsi", "disk", "mpath", "scsi"}:
        return "block-presented"
    if pool_type == "logical":
        return "logical"
    return "filesystem"


def list_libvirt_pools() -> list[dict[str, Any]]:
    result = run(["virsh", "pool-list", "--all"])
    pools = []
    if result.returncode != 0:
        return pools

    lines = _split_lines(result.stdout)
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("Name") or stripped.startswith("-"):
            continue

        parts = line.split()
        if len(parts) < 3:
            continue

        name, state, autostart = parts[0], parts[1], parts[2]
        info = run(["virsh", "pool-info", name])
        xml = run(["virsh", "pool-dumpxml", name])

        pool_bytes = _pool_capacity_bytes(xml.stdout)

        pool_type = _pool_type(xml.stdout)
        usage_mode = _pool_usage_mode(pool_type)

        used_percent = 0
        free_percent = 0
        chart_note = ""

        if pool_bytes["capacity_bytes"] > 0:
            if usage_mode == "block-presented":
                # iSCSI/block pools often report the whole LUN as allocated.
                # That is not filesystem free space, so do not present it as "100% full".
                used_percent = None
                free_percent = None
                chart_note = "Block-backed pool. Libvirt reports presented LUN capacity, not filesystem free space."
            else:
                used_percent = round((pool_bytes["allocation_bytes"] / pool_bytes["capacity_bytes"]) * 100, 1)
                free_percent = round((pool_bytes["available_bytes"] / pool_bytes["capacity_bytes"]) * 100, 1)

        if used_percent is None:
            chart_gradient = "conic-gradient(var(--muted, #777) 0 100%)"
        else:
            chart_gradient = f"conic-gradient(var(--danger, #d9534f) 0 {used_percent}%, var(--success, #5cb85c) {used_percent}% 100%)"

        source_name = _pool_source_name(xml.stdout)

        pool = {
            "name": name,
            "state": state,
            "autostart": autostart,
            "type": pool_type,
            "usage_mode": usage_mode,
            "source_name": source_name,
            "path": _pool_target_path(xml.stdout),
            "capacity": "",
            "allocation": "",
            "available": "",
            "capacity_bytes": pool_bytes["capacity_bytes"],
            "allocation_bytes": pool_bytes["allocation_bytes"],
            "available_bytes": pool_bytes["available_bytes"],
            "used_percent": used_percent,
            "free_percent": free_percent,
            "chart_note": chart_note,
            "chart_gradient": chart_gradient,
        }

        if pool_type == "logical" and source_name:
            thin = _lvm_thin_usage_for_vg(source_name)
            if thin:
                pool["usage_mode"] = "lvm-thin"
                pool["lvm_thin"] = thin
                pool["used_percent"] = thin.get("data_percent")
                pool["free_percent"] = thin.get("free_percent")
                pool["capacity"] = thin.get("thinpool_size") or pool["capacity"]
                data_percent = thin.get("data_percent")
                free_percent = thin.get("free_percent")

                if data_percent is not None:
                    pool["allocation"] = f"{data_percent}% data used"
                    pool["available"] = f"{free_percent}% data free"
                    pool["chart_gradient"] = f"conic-gradient(var(--danger, #d9534f) 0 {data_percent}%, var(--success, #5cb85c) {data_percent}% 100%)"
                else:
                    pool["allocation"] = "Unknown data usage"
                    pool["available"] = "Unknown free space"
                    pool["chart_gradient"] = "conic-gradient(var(--muted, #777) 0 100%)"

                pool["chart_note"] = "LVM-thin pool. Chart shows thin-pool data usage, not raw LUN allocation."


        if info.returncode == 0:
            for row in _split_lines(info.stdout):
                if ":" not in row:
                    continue
                key, value = row.split(":", 1)
                k = key.strip().lower()
                v = value.strip()
                if k == "capacity":
                    pool["capacity"] = v
                elif k == "allocation":
                    pool["allocation"] = v
                elif k == "available":
                    pool["available"] = v

        pools.append(pool)

    return pools


def list_filesystems() -> list[dict[str, Any]]:
    result = run(["findmnt", "-J", "-o", "TARGET,SOURCE,FSTYPE,SIZE,USED,AVAIL,USE%,OPTIONS"])
    if result.returncode != 0:
        return []

    try:
        data = json.loads(result.stdout)
    except Exception:
        return []

    rows = []

    def walk(items: list[dict[str, Any]]) -> None:
        for item in items:
            rows.append({
                "target": item.get("target", ""),
                "source": item.get("source", ""),
                "fstype": item.get("fstype", ""),
                "size": item.get("size", ""),
                "used": item.get("used", ""),
                "avail": item.get("avail", ""),
                "use_percent": item.get("use%", ""),
                "options": item.get("options", ""),
            })
            children = item.get("children") or []
            walk(children)

    walk(data.get("filesystems", []))
    return rows


def list_vm_disks() -> list[dict[str, str]]:
    vm_result = run(["virsh", "list", "--all", "--name"])
    if vm_result.returncode != 0:
        return []

    disks = []
    for vm in _split_lines(vm_result.stdout):
        blk = run(["virsh", "domblklist", vm, "--details"])
        if blk.returncode != 0:
            continue

        for line in _split_lines(blk.stdout):
            if line.startswith("Type") or line.startswith("-"):
                continue

            parts = line.split(None, 3)
            if len(parts) < 4:
                continue

            disk_type, device, target, source = parts
            disks.append({
                "vm": vm,
                "type": disk_type,
                "device": device,
                "target": target,
                "source": source,
            })

    return disks


def list_host_links() -> list[dict[str, Any]]:
    result = run(["ip", "-j", "addr", "show"])
    if result.returncode != 0:
        return []

    try:
        links = json.loads(result.stdout)
    except Exception:
        return []

    out = []
    for item in links:
        name = item.get("ifname", "")
        addrs = []
        for addr in item.get("addr_info", []):
            local = addr.get("local")
            prefix = addr.get("prefixlen")
            family = addr.get("family")
            if local and prefix is not None:
                addrs.append(f"{local}/{prefix} ({family})")

        out.append({
            "name": name,
            "state": item.get("operstate", ""),
            "mac": item.get("address", ""),
            "addresses": addrs,
        })

    return out


def default_route() -> dict[str, str]:
    result = run(["ip", "-j", "route", "show", "default"])
    if result.returncode != 0:
        return {}

    try:
        routes = json.loads(result.stdout)
    except Exception:
        return {}

    if not routes:
        return {}

    route = routes[0]
    return {
        "dev": route.get("dev", ""),
        "gateway": route.get("gateway", ""),
    }


def validate_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise RuntimeError("Name is required.")
    if not re.match(r"^[A-Za-z0-9_.-]+$", name):
        raise RuntimeError("Name may only contain letters, numbers, dots, dashes, and underscores.")
    return name


def validate_vlan(vlan_tag: str | None) -> str:
    vlan_tag = str(vlan_tag or "").strip()
    if not vlan_tag:
        return ""
    vlan_id = int(vlan_tag)
    if vlan_id < 1 or vlan_id > 4094:
        raise RuntimeError("VLAN tag must be between 1 and 4094.")
    return str(vlan_id)


def validate_cidr(ip_cidr: str) -> str:
    ip_cidr = (ip_cidr or "").strip()
    if not ip_cidr:
        raise RuntimeError("IP/CIDR is required for configured storage interfaces.")
    if "/" not in ip_cidr:
        raise RuntimeError("Use CIDR format, for example 10.60.0.10/24.")
    return ip_cidr


def list_storage_networks() -> dict[str, Any]:
    return read_json(STORAGE_NETWORKS_FILE)


def save_storage_network(
    name: str,
    mode: str,
    parent_interface: str,
    vlan_tag: str,
    ip_cidr: str,
    gateway: str,
    dns_servers: str,
    mtu: str,
    purpose: str,
    notes: str,
) -> dict[str, Any]:
    name = validate_name(name)
    mode = (mode or "").strip()

    if mode not in {"existing", "vlan", "dedicated"}:
        raise RuntimeError("Storage network mode must be existing, vlan, or dedicated.")

    parent_interface = (parent_interface or "").strip()
    if not parent_interface:
        raise RuntimeError("Parent/interface is required.")

    vlan_tag = validate_vlan(vlan_tag)

    if mode == "vlan" and not vlan_tag:
        raise RuntimeError("VLAN tag is required for VLAN storage networks.")

    if mode in {"vlan", "dedicated"}:
        ip_cidr = validate_cidr(ip_cidr)

    if mode == "existing":
        ip_cidr = (ip_cidr or "").strip()

    if mtu:
        mtu_int = int(mtu)
        if mtu_int < 576 or mtu_int > 9216:
            raise RuntimeError("MTU must be between 576 and 9216.")

    data = list_storage_networks()
    data[name] = {
        "name": name,
        "mode": mode,
        "parent_interface": parent_interface,
        "vlan_tag": vlan_tag,
        "interface_name": storage_interface_name(name, mode, parent_interface, vlan_tag),
        "ip_cidr": ip_cidr,
        "gateway": (gateway or "").strip(),
        "dns_servers": (dns_servers or "").strip(),
        "mtu": (mtu or "").strip(),
        "purpose": (purpose or "").strip(),
        "notes": (notes or "").strip(),
        "updated_at": int(time.time()),
    }
    write_json(STORAGE_NETWORKS_FILE, data)
    return data[name]


def delete_storage_network(name: str) -> None:
    name = validate_name(name)
    data = list_storage_networks()
    if name in data:
        del data[name]
        write_json(STORAGE_NETWORKS_FILE, data)


def storage_interface_name(name: str, mode: str, parent: str, vlan_tag: str) -> str:
    if mode == "existing":
        return parent

    if mode == "dedicated":
        return parent

    base = f"st{vlan_tag}-{name}"
    clean = re.sub(r"[^A-Za-z0-9_.-]", "", base)
    if len(clean) <= 15:
        return clean

    return f"st{vlan_tag}"[:15]


def is_default_route_interface(interface_name: str) -> bool:
    return default_route().get("dev") == interface_name


def apply_storage_network(name: str) -> dict[str, Any]:
    name = validate_name(name)
    data = list_storage_networks()
    if name not in data:
        raise RuntimeError("Storage network not found.")

    profile = data[name]
    mode = profile.get("mode", "")
    parent = profile.get("parent_interface", "")
    iface = profile.get("interface_name", "")
    vlan_tag = profile.get("vlan_tag", "")
    ip_cidr = profile.get("ip_cidr", "")
    gateway = profile.get("gateway", "")
    dns_servers = profile.get("dns_servers", "")
    mtu = profile.get("mtu", "")

    if mode == "existing":
        profile["last_apply_result"] = "Existing interface selected. No host network config written."
        profile["last_apply_at"] = int(time.time())
        data[name] = profile
        write_json(STORAGE_NETWORKS_FILE, data)
        return profile

    if mode == "dedicated" and is_default_route_interface(parent):
        raise RuntimeError(
            f"{parent} appears to be the default-route management interface. "
            "Refusing to write a dedicated storage config to it. Use VLAN mode or choose another NIC."
        )

    NETWORKD_DIR.mkdir(parents=True, exist_ok=True)

    written = []

    if mode == "vlan":
        netdev = NETWORKD_DIR / f"30-atlasvm-storage-{name}.netdev"
        network_parent = NETWORKD_DIR / f"30-atlasvm-storage-parent-{name}.network"
        network_iface = NETWORKD_DIR / f"31-atlasvm-storage-{name}.network"

        netdev.write_text(f"""[NetDev]
Name={iface}
Kind=vlan

[VLAN]
Id={vlan_tag}
""")

        network_parent.write_text(f"""[Match]
Name={parent}

[Network]
VLAN={iface}
""")

        network_iface.write_text(networkd_network_text(iface, ip_cidr, gateway, dns_servers, mtu))

        written.extend([str(netdev), str(network_parent), str(network_iface)])

    elif mode == "dedicated":
        network_iface = NETWORKD_DIR / f"31-atlasvm-storage-{name}.network"
        network_iface.write_text(networkd_network_text(parent, ip_cidr, gateway, dns_servers, mtu))
        written.append(str(network_iface))

    for path in written:
        os.chmod(path, 0o644)

    run(["systemctl", "enable", "--now", "systemd-networkd"], check=False)
    restart = run(["systemctl", "restart", "systemd-networkd"], check=False)

    profile["written_files"] = written
    profile["last_apply_at"] = int(time.time())
    profile["last_apply_result"] = "applied" if restart.returncode == 0 else restart.stderr
    data[name] = profile
    write_json(STORAGE_NETWORKS_FILE, data)

    return profile


def networkd_network_text(iface: str, ip_cidr: str, gateway: str, dns_servers: str, mtu: str) -> str:
    lines = [
        "[Match]",
        f"Name={iface}",
        "",
        "[Network]",
        f"Address={ip_cidr}",
        "LinkLocalAddressing=no",
        "IPv6AcceptRA=no",
    ]

    if gateway:
        lines.append(f"Gateway={gateway}")

    dns = dns_servers.replace(",", " ").strip()
    if dns:
        for server in dns.split():
            lines.append(f"DNS={server}")

    if mtu:
        lines.extend(["", "[Link]", f"MTUBytes={mtu}"])

    return "\n".join(lines) + "\n"


def reconcile_storage_networks() -> list[dict[str, Any]]:
    results = []
    for name in list_storage_networks().keys():
        try:
            results.append(apply_storage_network(name))
        except Exception as exc:
            results.append({"name": name, "error": str(exc)})
    return results

NFS_TARGETS_FILE = Path("/opt/atlasvm/atlasvm_nfs_targets.json")


def list_nfs_targets() -> dict[str, Any]:
    return read_json(NFS_TARGETS_FILE)


def validate_mount_path(path: str) -> str:
    path = (path or "").strip()
    if not path:
        raise RuntimeError("Mount path is required.")
    if not path.startswith("/atlasvm-storage/"):
        raise RuntimeError("Mount path must be under /atlasvm-storage/.")
    if ".." in Path(path).parts:
        raise RuntimeError("Mount path may not contain '..'.")
    return path


def validate_roles(roles: str) -> list[str]:
    allowed = {"backups", "iso", "templates", "vm-disks"}
    selected = []
    for role in (roles or "").replace(",", " ").split():
        role = role.strip()
        if not role:
            continue
        if role not in allowed:
            raise RuntimeError(f"Invalid storage role: {role}")
        selected.append(role)
    return sorted(set(selected))


def save_nfs_target(
    name: str,
    storage_network: str,
    server: str,
    export_path: str,
    mount_path: str,
    nfs_version: str,
    mount_options: str,
    roles: str,
    create_libvirt_pool: str,
    libvirt_pool_name: str,
) -> dict[str, Any]:
    name = validate_name(name)
    server = (server or "").strip()
    export_path = (export_path or "").strip()
    mount_path = validate_mount_path(mount_path)

    if not server:
        raise RuntimeError("NFS server is required.")
    if not export_path.startswith("/"):
        raise RuntimeError("NFS export path must start with /.")

    nfs_version = (nfs_version or "4").strip()
    if nfs_version not in {"3", "4", "4.1", "4.2"}:
        raise RuntimeError("NFS version must be 3, 4, 4.1, or 4.2.")

    role_list = validate_roles(roles)
    create_pool = str(create_libvirt_pool or "").lower() in {"1", "true", "yes", "on"}

    if create_pool:
        libvirt_pool_name = validate_name(libvirt_pool_name or f"atlasvm-nfs-{name}")
    else:
        libvirt_pool_name = ""

    data = list_nfs_targets()
    data[name] = {
        "name": name,
        "storage_network": (storage_network or "").strip(),
        "server": server,
        "export_path": export_path,
        "mount_path": mount_path,
        "nfs_version": nfs_version,
        "mount_options": (mount_options or "rw,_netdev,noatime").strip(),
        "roles": role_list,
        "create_libvirt_pool": create_pool,
        "libvirt_pool_name": libvirt_pool_name,
        "updated_at": int(time.time()),
    }
    write_json(NFS_TARGETS_FILE, data)
    return data[name]


def delete_nfs_target(name: str) -> None:
    name = validate_name(name)
    data = list_nfs_targets()
    if name in data:
        del data[name]
        write_json(NFS_TARGETS_FILE, data)


def nfs_unit_name(name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "-", name)
    return f"atlasvm-nfs-{safe}.mount"


def systemd_escape_path(path: str) -> str:
    result = run(["systemd-escape", "-p", "--suffix=mount", path])
    if result.returncode == 0:
        return result.stdout.strip()
    return path.strip("/").replace("/", "-") + ".mount"


def test_nfs_target(name: str) -> dict[str, Any]:
    name = validate_name(name)
    data = list_nfs_targets()
    if name not in data:
        raise RuntimeError("NFS target not found.")

    target = data[name]
    server = target["server"]
    export_path = target["export_path"]

    showmount = run(["showmount", "-e", server], check=False)

    # This validates basic reachability. Some NFSv4 servers block showmount,
    # because standards apparently needed suspense.
    return {
        "name": name,
        "server": server,
        "export_path": export_path,
        "showmount_returncode": showmount.returncode,
        "showmount_stdout": showmount.stdout,
        "showmount_stderr": showmount.stderr,
    }


def write_nfs_systemd_mount(target: dict[str, Any]) -> Path:
    mount_path = target["mount_path"]
    unit_name = systemd_escape_path(mount_path)
    unit_path = Path("/etc/systemd/system") / unit_name

    server = target["server"]
    export_path = target["export_path"]
    nfs_version = target["nfs_version"]
    options = target.get("mount_options") or "rw,_netdev,noatime"

    Path(mount_path).mkdir(parents=True, exist_ok=True)

    unit_path.write_text(f"""[Unit]
Description=AtlasVM NFS Mount {target['name']}
After=network-online.target
Wants=network-online.target

[Mount]
What={server}:{export_path}
Where={mount_path}
Type=nfs
Options={options},vers={nfs_version}

[Install]
WantedBy=multi-user.target
""")
    os.chmod(unit_path, 0o644)
    return unit_path


def ensure_libvirt_dir_pool(pool_name: str, path: str) -> None:
    existing = run(["virsh", "pool-info", pool_name], check=False)
    if existing.returncode != 0:
        run(["virsh", "pool-define-as", pool_name, "dir", "--target", path], check=True)

    run(["virsh", "pool-build", pool_name], check=False)
    run(["virsh", "pool-start", pool_name], check=False)
    run(["virsh", "pool-autostart", pool_name], check=False)


def apply_nfs_target(name: str) -> dict[str, Any]:
    name = validate_name(name)
    data = list_nfs_targets()
    if name not in data:
        raise RuntimeError("NFS target not found.")

    target = data[name]
    unit_path = write_nfs_systemd_mount(target)

    run(["systemctl", "daemon-reload"], check=False)
    run(["systemctl", "enable", unit_path.name], check=False)
    mount_result = run(["systemctl", "restart", unit_path.name], check=False)

    target["systemd_unit"] = unit_path.name
    target["last_apply_at"] = int(time.time())
    target["last_mount_returncode"] = mount_result.returncode
    target["last_mount_stdout"] = mount_result.stdout
    target["last_mount_stderr"] = mount_result.stderr

    mounted = run(["findmnt", "-n", target["mount_path"]], check=False)
    target["mounted"] = mounted.returncode == 0

    if target["mounted"] and target.get("create_libvirt_pool") and target.get("libvirt_pool_name"):
        ensure_libvirt_dir_pool(target["libvirt_pool_name"], target["mount_path"])

    data[name] = target
    write_json(NFS_TARGETS_FILE, data)
    return target


def unmount_nfs_target(name: str) -> dict[str, Any]:
    name = validate_name(name)
    data = list_nfs_targets()
    if name not in data:
        raise RuntimeError("NFS target not found.")

    target = data[name]
    unit = target.get("systemd_unit") or systemd_escape_path(target["mount_path"])

    run(["systemctl", "disable", "--now", unit], check=False)
    target["mounted"] = False
    target["last_unmount_at"] = int(time.time())

    data[name] = target
    write_json(NFS_TARGETS_FILE, data)
    return target

SMB_TARGETS_FILE = Path("/opt/atlasvm/atlasvm_smb_targets.json")
SMB_CREDENTIALS_DIR = Path("/etc/atlasvm/smb-credentials")


def list_smb_targets() -> dict[str, Any]:
    return read_json(SMB_TARGETS_FILE)


def save_smb_target(
    name: str,
    storage_network: str,
    server: str,
    share_name: str,
    mount_path: str,
    username: str,
    password: str,
    domain: str,
    smb_version: str,
    mount_options: str,
    roles: str,
    create_libvirt_pool: str,
    libvirt_pool_name: str,
) -> dict[str, Any]:
    name = validate_name(name)
    server = (server or "").strip()
    share_name = (share_name or "").strip().strip("/")
    mount_path = validate_mount_path(mount_path)

    if not server:
        raise RuntimeError("SMB server is required.")

    if not share_name:
        raise RuntimeError("SMB share name is required.")

    smb_version = (smb_version or "3.1.1").strip()
    if smb_version not in {"2.0", "2.1", "3.0", "3.02", "3.1.1"}:
        raise RuntimeError("SMB version must be 2.0, 2.1, 3.0, 3.02, or 3.1.1.")

    role_list = validate_roles(roles)
    create_pool = str(create_libvirt_pool or "").lower() in {"1", "true", "yes", "on"}

    if create_pool:
        libvirt_pool_name = validate_name(libvirt_pool_name or f"atlasvm-smb-{name}")
    else:
        libvirt_pool_name = ""

    data = list_smb_targets()
    existing = data.get(name, {})

    target = {
        "name": name,
        "storage_network": (storage_network or "").strip(),
        "server": server,
        "share_name": share_name,
        "mount_path": mount_path,
        "username": (username or "").strip(),
        "domain": (domain or "").strip(),
        "smb_version": smb_version,
        "mount_options": (mount_options or "rw,_netdev,noserverino,iocharset=utf8").strip(),
        "roles": role_list,
        "create_libvirt_pool": create_pool,
        "libvirt_pool_name": libvirt_pool_name,
        "updated_at": int(time.time()),
    }

    # Preserve existing credential file if password was not re-entered.
    if password:
        cred_file = write_smb_credentials(name, target["username"], password, target["domain"])
        target["credentials_file"] = str(cred_file)
    else:
        target["credentials_file"] = existing.get("credentials_file", "")

    data[name] = target
    write_json(SMB_TARGETS_FILE, data)
    return target


def write_smb_credentials(name: str, username: str, password: str, domain: str = "") -> Path:
    SMB_CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(SMB_CREDENTIALS_DIR, 0o700)

    cred_file = SMB_CREDENTIALS_DIR / f"{validate_name(name)}.cred"

    lines = [
        f"username={username}",
        f"password={password}",
    ]

    if domain:
        lines.append(f"domain={domain}")

    cred_file.write_text("\n".join(lines) + "\n")
    os.chmod(cred_file, 0o600)
    return cred_file


def delete_smb_target(name: str) -> None:
    name = validate_name(name)
    data = list_smb_targets()

    target = data.get(name, {})
    cred_file = target.get("credentials_file", "")
    if cred_file:
        try:
            Path(cred_file).unlink(missing_ok=True)
        except Exception:
            pass

    if name in data:
        del data[name]
        write_json(SMB_TARGETS_FILE, data)


def smb_unc(server: str, share_name: str) -> str:
    server = server.strip().strip("/")
    share_name = share_name.strip().strip("/")
    return f"//{server}/{share_name}"


def test_smb_target(name: str) -> dict[str, Any]:
    name = validate_name(name)
    data = list_smb_targets()

    if name not in data:
        raise RuntimeError("SMB target not found.")

    target = data[name]
    unc = smb_unc(target["server"], target["share_name"])

    # smbclient may not be installed. If it is, use it. If not, we still validate by attempting mount during apply.
    smbclient = run(["which", "smbclient"], check=False)
    if smbclient.returncode != 0:
        return {
            "name": name,
            "unc": unc,
            "test_available": False,
            "message": "smbclient is not installed. Install smbclient for pre-mount share tests, or use Apply/Mount.",
        }

    cmd = ["smbclient", "-L", f"//{target['server']}", "-m", "SMB3"]

    cred_file = target.get("credentials_file")
    if cred_file:
        cmd.extend(["-A", cred_file])
    else:
        cmd.extend(["-N"])

    result = run(cmd, check=False)

    return {
        "name": name,
        "unc": unc,
        "test_available": True,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def write_smb_systemd_mount(target: dict[str, Any]) -> Path:
    mount_path = target["mount_path"]
    unit_name = systemd_escape_path(mount_path)
    unit_path = Path("/etc/systemd/system") / unit_name

    Path(mount_path).mkdir(parents=True, exist_ok=True)

    unc = smb_unc(target["server"], target["share_name"])
    options = target.get("mount_options") or "rw,_netdev,noserverino,iocharset=utf8"
    smb_version = target.get("smb_version") or "3.1.1"
    cred_file = target.get("credentials_file", "")

    option_parts = [options, f"vers={smb_version}"]

    if cred_file:
        option_parts.append(f"credentials={cred_file}")
    else:
        option_parts.append("guest")

    # Make mounted files usable by libvirt/qemu where needed.
    # These can be overridden by user mount_options if we later add advanced UI.
    option_text = ",".join(part.strip(",") for part in option_parts if part)

    unit_path.write_text(f"""[Unit]
Description=AtlasVM SMB Mount {target['name']}
After=network-online.target
Wants=network-online.target

[Mount]
What={unc}
Where={mount_path}
Type=cifs
Options={option_text}

[Install]
WantedBy=multi-user.target
""")

    os.chmod(unit_path, 0o644)
    return unit_path


def apply_smb_target(name: str) -> dict[str, Any]:
    name = validate_name(name)
    data = list_smb_targets()

    if name not in data:
        raise RuntimeError("SMB target not found.")

    target = data[name]

    if target.get("username") and not target.get("credentials_file"):
        raise RuntimeError("Username is set but no credentials file exists. Re-save the SMB target with the password.")

    unit_path = write_smb_systemd_mount(target)

    run(["systemctl", "daemon-reload"], check=False)
    run(["systemctl", "enable", unit_path.name], check=False)
    mount_result = run(["systemctl", "restart", unit_path.name], check=False)

    target["systemd_unit"] = unit_path.name
    target["last_apply_at"] = int(time.time())
    target["last_mount_returncode"] = mount_result.returncode
    target["last_mount_stdout"] = mount_result.stdout
    target["last_mount_stderr"] = mount_result.stderr

    mounted = run(["findmnt", "-n", target["mount_path"]], check=False)
    target["mounted"] = mounted.returncode == 0

    if target["mounted"] and target.get("create_libvirt_pool") and target.get("libvirt_pool_name"):
        ensure_libvirt_dir_pool(target["libvirt_pool_name"], target["mount_path"])

    data[name] = target
    write_json(SMB_TARGETS_FILE, data)
    return target


def unmount_smb_target(name: str) -> dict[str, Any]:
    name = validate_name(name)
    data = list_smb_targets()

    if name not in data:
        raise RuntimeError("SMB target not found.")

    target = data[name]
    unit = target.get("systemd_unit") or systemd_escape_path(target["mount_path"])

    run(["systemctl", "disable", "--now", unit], check=False)
    target["mounted"] = False
    target["last_unmount_at"] = int(time.time())

    data[name] = target
    write_json(SMB_TARGETS_FILE, data)
    return target

ISCSI_TARGETS_FILE = Path("/opt/atlasvm/atlasvm_iscsi_targets.json")


def list_iscsi_targets() -> dict[str, Any]:
    return read_json(ISCSI_TARGETS_FILE)


def save_iscsi_target(
    name: str,
    storage_network: str,
    portal: str,
    target_iqn: str,
    username: str,
    password: str,
    mutual_username: str,
    mutual_password: str,
    roles: str,
    create_libvirt_pool: str,
    libvirt_pool_name: str,
    notes: str,
) -> dict[str, Any]:
    name = validate_name(name)
    portal = (portal or "").strip()
    target_iqn = (target_iqn or "").strip()

    if not portal:
        raise RuntimeError("iSCSI portal is required. Example: 10.60.0.20 or 10.60.0.20:3260.")

    if not target_iqn:
        raise RuntimeError("Target IQN is required. Use Discover first if needed.")

    role_list = validate_roles(roles)
    create_pool = str(create_libvirt_pool or "").lower() in {"1", "true", "yes", "on"}

    if create_pool:
        libvirt_pool_name = validate_name(libvirt_pool_name or f"atlasvm-iscsi-{name}")
    else:
        libvirt_pool_name = ""

    data = list_iscsi_targets()
    existing = data.get(name, {})

    target = {
        "name": name,
        "storage_network": (storage_network or "").strip(),
        "portal": portal,
        "target_iqn": target_iqn,
        "username": (username or "").strip(),
        "mutual_username": (mutual_username or "").strip(),
        "roles": role_list,
        "create_libvirt_pool": create_pool,
        "libvirt_pool_name": libvirt_pool_name,
        "notes": (notes or "").strip(),
        "updated_at": int(time.time()),
    }

    # Do not write CHAP secrets into the JSON state. Store separately.
    if password or mutual_password:
        secret_file = write_iscsi_secrets(
            name=name,
            username=target["username"],
            password=password,
            mutual_username=target["mutual_username"],
            mutual_password=mutual_password,
        )
        target["secrets_file"] = str(secret_file)
        target["chap_configured"] = bool(password)
        target["mutual_chap_configured"] = bool(mutual_password)
    else:
        target["secrets_file"] = existing.get("secrets_file", "")
        target["chap_configured"] = existing.get("chap_configured", False)
        target["mutual_chap_configured"] = existing.get("mutual_chap_configured", False)

    data[name] = target
    write_json(ISCSI_TARGETS_FILE, data)
    return target


def iscsi_secrets_dir() -> Path:
    d = Path("/etc/atlasvm/iscsi-secrets")
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    return d


def write_iscsi_secrets(
    name: str,
    username: str,
    password: str,
    mutual_username: str,
    mutual_password: str,
) -> Path:
    path = iscsi_secrets_dir() / f"{validate_name(name)}.json"

    data = {
        "username": username or "",
        "password": password or "",
        "mutual_username": mutual_username or "",
        "mutual_password": mutual_password or "",
    }

    path.write_text(json.dumps(data, indent=2))
    os.chmod(path, 0o600)
    return path


def read_iscsi_secrets(path: str) -> dict[str, str]:
    if not path:
        return {}

    p = Path(path)
    if not p.exists():
        return {}

    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def delete_iscsi_target(name: str) -> None:
    name = validate_name(name)
    data = list_iscsi_targets()

    target = data.get(name, {})
    secrets_file = target.get("secrets_file", "")
    if secrets_file:
        try:
            Path(secrets_file).unlink(missing_ok=True)
        except Exception:
            pass

    if name in data:
        del data[name]
        write_json(ISCSI_TARGETS_FILE, data)


def iscsi_discover(portal: str) -> dict[str, Any]:
    portal = (portal or "").strip()
    if not portal:
        raise RuntimeError("Portal is required.")

    result = run(["iscsiadm", "-m", "discovery", "-t", "sendtargets", "-p", portal], check=False)

    targets = []
    if result.returncode == 0:
        for line in _split_lines(result.stdout):
            parts = line.split()
            if len(parts) >= 2:
                targets.append({
                    "portal": parts[0],
                    "target_iqn": parts[1],
                    "raw": line,
                })

    return {
        "portal": portal,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "targets": targets,
    }


def test_iscsi_target(name: str) -> dict[str, Any]:
    name = validate_name(name)
    data = list_iscsi_targets()

    if name not in data:
        raise RuntimeError("iSCSI target not found.")

    target = data[name]
    discovery = iscsi_discover(target["portal"])

    found = False
    for t in discovery.get("targets", []):
        if t.get("target_iqn") == target["target_iqn"]:
            found = True
            break

    return {
        "name": name,
        "portal": target["portal"],
        "target_iqn": target["target_iqn"],
        "found": found,
        "discovery": discovery,
    }


def configure_iscsi_node(target: dict[str, Any]) -> None:
    portal = target["portal"]
    iqn = target["target_iqn"]

    # Ensure node record exists.
    run(["iscsiadm", "-m", "discovery", "-t", "sendtargets", "-p", portal], check=False)

    run([
        "iscsiadm", "-m", "node",
        "-T", iqn,
        "-p", portal,
        "--op", "update",
        "-n", "node.startup",
        "-v", "automatic",
    ], check=False)

    secrets = read_iscsi_secrets(target.get("secrets_file", ""))

    username = secrets.get("username") or target.get("username") or ""
    password = secrets.get("password") or ""
    mutual_username = secrets.get("mutual_username") or target.get("mutual_username") or ""
    mutual_password = secrets.get("mutual_password") or ""

    if username and password:
        updates = [
            ("node.session.auth.authmethod", "CHAP"),
            ("node.session.auth.username", username),
            ("node.session.auth.password", password),
        ]

        if mutual_username and mutual_password:
            updates.extend([
                ("node.session.auth.username_in", mutual_username),
                ("node.session.auth.password_in", mutual_password),
            ])

        for key, value in updates:
            run([
                "iscsiadm", "-m", "node",
                "-T", iqn,
                "-p", portal,
                "--op", "update",
                "-n", key,
                "-v", value,
            ], check=False)


def login_iscsi_target(name: str) -> dict[str, Any]:
    name = validate_name(name)
    data = list_iscsi_targets()

    if name not in data:
        raise RuntimeError("iSCSI target not found.")

    target = data[name]
    configure_iscsi_node(target)

    before = list_iscsi_sessions()
    result = run([
        "iscsiadm", "-m", "node",
        "-T", target["target_iqn"],
        "-p", target["portal"],
        "--login",
    ], check=False)
    after = list_iscsi_sessions()

    target["last_login_at"] = int(time.time())
    target["last_login_returncode"] = result.returncode
    target["last_login_stdout"] = result.stdout
    target["last_login_stderr"] = result.stderr
    target["sessions_before"] = before
    target["sessions_after"] = after
    target["logged_in"] = iscsi_session_exists(target["target_iqn"], target["portal"], after)
    target["block_devices"] = list_iscsi_block_devices()

    if target["logged_in"] and target.get("create_libvirt_pool") and target.get("libvirt_pool_name"):
        # For now, define a libvirt iscsi pool referencing the target.
        # Disk/LVM formatting comes later because block storage deserves at least one adult in the room.
        ensure_libvirt_iscsi_pool(
            pool_name=target["libvirt_pool_name"],
            portal=target["portal"],
            target_iqn=target["target_iqn"],
        )

    data[name] = target
    write_json(ISCSI_TARGETS_FILE, data)
    return target


def logout_iscsi_target(name: str) -> dict[str, Any]:
    name = validate_name(name)
    data = list_iscsi_targets()

    if name not in data:
        raise RuntimeError("iSCSI target not found.")

    target = data[name]
    result = run([
        "iscsiadm", "-m", "node",
        "-T", target["target_iqn"],
        "-p", target["portal"],
        "--logout",
    ], check=False)

    target["last_logout_at"] = int(time.time())
    target["last_logout_returncode"] = result.returncode
    target["last_logout_stdout"] = result.stdout
    target["last_logout_stderr"] = result.stderr
    target["logged_in"] = False
    target["block_devices"] = list_iscsi_block_devices()

    data[name] = target
    write_json(ISCSI_TARGETS_FILE, data)
    return target


def list_iscsi_sessions() -> list[dict[str, str]]:
    result = run(["iscsiadm", "-m", "session"], check=False)
    sessions = []

    if result.returncode != 0:
        return sessions

    for line in _split_lines(result.stdout):
        # Example:
        # tcp: [1] 10.60.0.20:3260,1 iqn.2026-...
        parts = line.split()
        if len(parts) >= 4:
            sessions.append({
                "transport": parts[0].rstrip(":"),
                "sid": parts[1].strip("[]"),
                "portal": parts[2].rstrip(","),
                "target_iqn": parts[3],
                "raw": line,
            })
        else:
            sessions.append({"raw": line})

    return sessions


def iscsi_session_exists(iqn: str, portal: str, sessions: list[dict[str, str]] | None = None) -> bool:
    sessions = sessions if sessions is not None else list_iscsi_sessions()
    portal_host = portal.split(",")[0]

    for s in sessions:
        if s.get("target_iqn") == iqn and portal_host in s.get("portal", ""):
            return True

    return False


def list_iscsi_block_devices() -> list[dict[str, str]]:
    rows = []

    # Use lsblk as primary view.
    result = run(["lsblk", "-J", "-o", "NAME,PATH,SIZE,TYPE,MODEL,SERIAL,TRAN,MOUNTPOINTS"], check=False)
    if result.returncode != 0:
        return rows

    try:
        data = json.loads(result.stdout)
    except Exception:
        return rows

    def walk(items: list[dict[str, Any]]) -> None:
        for item in items:
            tran = str(item.get("tran") or "")
            model = str(item.get("model") or "")
            path = str(item.get("path") or "")
            if tran == "iscsi" or "iscsi" in model.lower() or "/dev/disk/by-path" in path:
                rows.append({
                    "name": item.get("name", ""),
                    "path": item.get("path", ""),
                    "size": item.get("size", ""),
                    "type": item.get("type", ""),
                    "model": item.get("model", ""),
                    "serial": item.get("serial", ""),
                    "tran": item.get("tran", ""),
                    "mountpoints": ",".join(item.get("mountpoints") or []),
                })

            walk(item.get("children") or [])

    walk(data.get("blockdevices", []))

    # Also include by-path iSCSI symlinks.
    bypath = Path("/dev/disk/by-path")
    if bypath.exists():
        for item in bypath.glob("*iscsi*"):
            try:
                resolved = str(item.resolve())
            except Exception:
                resolved = ""
            rows.append({
                "name": item.name,
                "path": resolved,
                "size": "",
                "type": "by-path",
                "model": "",
                "serial": "",
                "tran": "iscsi",
                "mountpoints": "",
            })

    # De-duplicate by path/name.
    seen = set()
    unique = []
    for row in rows:
        key = (row.get("name"), row.get("path"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)

    return unique


def ensure_libvirt_iscsi_pool(pool_name: str, portal: str, target_iqn: str) -> None:
    existing = run(["virsh", "pool-info", pool_name], check=False)
    if existing.returncode == 0:
        run(["virsh", "pool-start", pool_name], check=False)
        run(["virsh", "pool-autostart", pool_name], check=False)
        return

    host = portal.split(":")[0]
    port = "3260"
    if ":" in portal:
        host, port = portal.rsplit(":", 1)

    xml = f"""<pool type='iscsi'>
  <name>{pool_name}</name>
  <source>
    <host name='{host}' port='{port}'/>
    <device path='{target_iqn}'/>
  </source>
  <target>
    <path>/dev/disk/by-path</path>
  </target>
</pool>
"""

    tmp = Path(f"/tmp/{pool_name}.xml")
    tmp.write_text(xml)

    run(["virsh", "pool-define", str(tmp)], check=False)
    run(["virsh", "pool-start", pool_name], check=False)
    run(["virsh", "pool-autostart", pool_name], check=False)

    try:
        tmp.unlink()
    except Exception:
        pass

def list_iscsi_device_details() -> list[dict[str, Any]]:
    """
    Detailed iSCSI LUN visibility.

    This does not attempt to infer array-side thin provisioning. It reports what
    the host can actually see: by-path links, resolved block devices, partitions,
    filesystems, mounts, LVM membership, and VM disk references.
    """
    devices: list[dict[str, Any]] = []

    lsblk_map = _lsblk_device_map()
    vm_sources = list_vm_disks()

    bypath = Path("/dev/disk/by-path")
    if not bypath.exists():
        return devices

    for item in sorted(bypath.glob("*iscsi*")):
        try:
            resolved = str(item.resolve())
        except Exception:
            resolved = ""

        meta = lsblk_map.get(resolved, {})
        children = meta.get("children", []) or []

        partitions = []
        for child in children:
            partitions.append({
                "name": child.get("name", ""),
                "path": child.get("path", ""),
                "size": child.get("size", ""),
                "type": child.get("type", ""),
                "fstype": child.get("fstype", ""),
                "mountpoints": ",".join(child.get("mountpoints") or []),
            })

        mountpoints = ",".join(meta.get("mountpoints") or [])
        fstype = meta.get("fstype", "")

        used_by_vms = []
        for disk in vm_sources:
            source = disk.get("source", "")
            if source and (source == resolved or source == str(item) or resolved in source or item.name in source):
                used_by_vms.append({
                    "vm": disk.get("vm", ""),
                    "target": disk.get("target", ""),
                    "source": source,
                })

        devices.append({
            "by_path": str(item),
            "by_path_name": item.name,
            "resolved_path": resolved,
            "name": meta.get("name", ""),
            "size": meta.get("size", ""),
            "type": meta.get("type", ""),
            "model": meta.get("model", ""),
            "serial": meta.get("serial", ""),
            "fstype": fstype,
            "mountpoints": mountpoints,
            "partitions": partitions,
            "used_by_vms": used_by_vms,
            "lvm": _lvm_membership_for_device(resolved),
            "filesystem_usage": _filesystem_usage_for_path(mountpoints),
        })

    return devices


def _lsblk_device_map() -> dict[str, Any]:
    result = run([
        "lsblk",
        "-J",
        "-o",
        "NAME,PATH,SIZE,TYPE,FSTYPE,MODEL,SERIAL,TRAN,MOUNTPOINTS",
    ], check=False)

    if result.returncode != 0:
        return {}

    try:
        data = json.loads(result.stdout)
    except Exception:
        return {}

    out: dict[str, Any] = {}

    def walk(items: list[dict[str, Any]]) -> None:
        for item in items:
            path = item.get("path", "")
            name = item.get("name", "")

            if path:
                out[path] = item
            if name:
                out[f"/dev/{name}"] = item

            walk(item.get("children") or [])

    walk(data.get("blockdevices", []))
    return out


def _lvm_membership_for_device(device_path: str) -> dict[str, str]:
    if not device_path:
        return {}

    result = run([
        "pvs",
        "--noheadings",
        "--readonly",
        "--reportformat", "json",
        "-o", "pv_name,vg_name,pv_size,pv_free",
    ], check=False)

    if result.returncode != 0:
        return {}

    try:
        data = json.loads(result.stdout)
    except Exception:
        return {}

    for report in data.get("report", []):
        for pv in report.get("pv", []):
            if pv.get("pv_name") == device_path:
                return {
                    "pv_name": pv.get("pv_name", ""),
                    "vg_name": pv.get("vg_name", ""),
                    "pv_size": pv.get("pv_size", ""),
                    "pv_free": pv.get("pv_free", ""),
                }

    return {}


def _filesystem_usage_for_path(mountpoints: str) -> dict[str, str]:
    if not mountpoints:
        return {}

    mountpoint = mountpoints.split(",")[0].strip()
    if not mountpoint:
        return {}

    result = run([
        "df",
        "-h",
        "--output=source,size,used,avail,pcent,target",
        mountpoint,
    ], check=False)

    if result.returncode != 0:
        return {}

    lines = _split_lines(result.stdout)
    if len(lines) < 2:
        return {}

    parts = lines[1].split(None, 5)
    if len(parts) < 6:
        return {}

    return {
        "source": parts[0],
        "size": parts[1],
        "used": parts[2],
        "avail": parts[3],
        "pcent": parts[4],
        "target": parts[5],
    }


def list_lvm_storage_summary() -> dict[str, Any]:
    return {
        "pvs": _lvm_json(["pvs", "--readonly", "--reportformat", "json", "-o", "pv_name,vg_name,pv_size,pv_free"]),
        "vgs": _lvm_json(["vgs", "--readonly", "--reportformat", "json", "-o", "vg_name,vg_size,vg_free,pv_count,lv_count"]),
        "lvs": _lvm_json(["lvs", "--readonly", "--reportformat", "json", "-a", "-o", "lv_name,vg_name,lv_size,pool_lv,data_percent,metadata_percent,origin,devices"]),
    }


def _lvm_json(cmd: list[str]) -> list[dict[str, Any]]:
    result = run(cmd, check=False)
    if result.returncode != 0:
        return []

    try:
        data = json.loads(result.stdout)
    except Exception:
        return []

    rows = []
    for report in data.get("report", []):
        for key, values in report.items():
            if isinstance(values, list):
                rows.extend(values)
    return rows


def iscsi_lvm_candidate_devices(name: str) -> list[dict[str, Any]]:
    """
    Return iSCSI devices that could be initialized as LVM-backed storage.

    This is deliberately conservative. We do not want AtlasVM helpfully formatting
    the wrong disk, because that is how automation becomes evidence.
    """
    name = validate_name(name)
    targets = list_iscsi_targets()

    if name not in targets:
        raise RuntimeError("iSCSI target not found.")

    devices = list_iscsi_device_details()
    candidates = []

    for d in devices:
        by_path = d.get("by_path", "")
        resolved = d.get("resolved_path", "")

        if not by_path or "iscsi" not in by_path:
            continue

        candidate = dict(d)
        candidate["eligible"] = True
        candidate["warnings"] = []

        if d.get("partitions"):
            candidate["eligible"] = False
            candidate["warnings"].append("Device has partitions.")

        if d.get("fstype"):
            candidate["eligible"] = False
            candidate["warnings"].append("Device already has a filesystem.")

        if d.get("mountpoints"):
            candidate["eligible"] = False
            candidate["warnings"].append("Device is mounted.")

        if d.get("lvm"):
            candidate["eligible"] = False
            candidate["warnings"].append("Device is already an LVM physical volume.")

        if d.get("used_by_vms"):
            candidate["eligible"] = False
            candidate["warnings"].append("Device is already referenced by a VM.")

        if not resolved:
            candidate["eligible"] = False
            candidate["warnings"].append("Could not resolve by-path device.")

        candidates.append(candidate)

    return candidates


def _run_required(cmd: list[str]) -> subprocess.CompletedProcess:
    result = run(cmd, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            "Command failed: "
            + " ".join(cmd)
            + "\nSTDOUT:\n"
            + result.stdout
            + "\nSTDERR:\n"
            + result.stderr
        )
    return result


def initialize_iscsi_lvm_thin(
    name: str,
    by_path: str,
    vg_name: str,
    thinpool_name: str,
    thinpool_percent: str,
    create_libvirt_pool: str,
    libvirt_pool_name: str,
    confirm_text: str,
) -> dict[str, Any]:
    """
    Initialize an iSCSI LUN as an LVM-thin storage backend.

    This is destructive to the selected block device. The caller must provide
    confirm_text == DESTROY. Yes, theatrical, but so is data loss.
    """
    name = validate_name(name)
    vg_name = validate_name(vg_name)
    thinpool_name = validate_name(thinpool_name)

    if confirm_text != "DESTROY":
        raise RuntimeError("Type DESTROY to confirm LVM-thin initialization.")

    by_path = (by_path or "").strip()

    if not by_path.startswith("/dev/disk/by-path/") or "iscsi" not in by_path:
        raise RuntimeError("Selected device must be an iSCSI /dev/disk/by-path device.")

    thinpool_percent = str(thinpool_percent or "95").strip()
    percent = int(thinpool_percent)

    if percent < 50 or percent > 99:
        raise RuntimeError("Thin pool percent must be between 50 and 99.")

    candidates = iscsi_lvm_candidate_devices(name)
    selected = None

    for c in candidates:
        if c.get("by_path") == by_path:
            selected = c
            break

    if not selected:
        raise RuntimeError("Selected iSCSI device was not found.")

    if not selected.get("eligible"):
        raise RuntimeError("Selected iSCSI device is not eligible: " + "; ".join(selected.get("warnings", [])))

    resolved = selected.get("resolved_path")
    if not resolved:
        raise RuntimeError("Could not resolve selected iSCSI device.")

    targets = list_iscsi_targets()
    if name not in targets:
        raise RuntimeError("iSCSI target not found.")

    target = targets[name]

    # Refuse to reuse an existing VG name.
    existing_vg = run(["vgs", "--noheadings", "-o", "vg_name", vg_name], check=False)
    if existing_vg.returncode == 0 and vg_name in existing_vg.stdout:
        raise RuntimeError(f"Volume group already exists: {vg_name}")

    commands = []

    commands.append(["wipefs", "-a", resolved])
    commands.append(["pvcreate", "-ff", "-y", resolved])
    commands.append(["vgcreate", vg_name, resolved])
    commands.append(["lvcreate", "-l", f"{percent}%VG", "-T", f"{vg_name}/{thinpool_name}"])

    command_results = []

    for cmd in commands:
        result = _run_required(cmd)
        command_results.append({
            "cmd": " ".join(cmd),
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        })

    create_pool = str(create_libvirt_pool or "").lower() in {"1", "true", "yes", "on"}

    if create_pool:
        pool_name = validate_name(libvirt_pool_name or f"atlasvm-lvm-{name}")
        ensure_libvirt_lvm_pool(pool_name, vg_name)
    else:
        pool_name = ""

    target["lvm_thin"] = {
        "enabled": True,
        "by_path": by_path,
        "resolved_path": resolved,
        "vg_name": vg_name,
        "thinpool_name": thinpool_name,
        "thinpool_percent": percent,
        "libvirt_pool_name": pool_name,
        "initialized_at": int(time.time()),
        "commands": command_results,
    }

    targets[name] = target
    write_json(ISCSI_TARGETS_FILE, targets)

    return target


def ensure_libvirt_lvm_pool(pool_name: str, vg_name: str) -> None:
    existing = run(["virsh", "pool-info", pool_name], check=False)
    if existing.returncode == 0:
        run(["virsh", "pool-start", pool_name], check=False)
        run(["virsh", "pool-autostart", pool_name], check=False)
        return

    xml = f"""<pool type='logical'>
  <name>{pool_name}</name>
  <source>
    <name>{vg_name}</name>
    <format type='lvm2'/>
  </source>
  <target>
    <path>/dev/{vg_name}</path>
  </target>
</pool>
"""

    tmp = Path(f"/tmp/{pool_name}.xml")
    tmp.write_text(xml)

    try:
        _run_required(["virsh", "pool-define", str(tmp)])
        run(["virsh", "pool-start", pool_name], check=False)
        run(["virsh", "pool-autostart", pool_name], check=False)
    finally:
        try:
            tmp.unlink()
        except Exception:
            pass
