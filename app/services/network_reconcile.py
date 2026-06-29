from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import libvirt


META_FILE = Path("/opt/atlasvm/atlasvm_networks.json")
LOG_FILE = Path("/var/log/atlasvm-network-reconcile.log")
LIBVIRT_URI = "qemu:///system"


def log(message: str) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a") as f:
        f.write(message.rstrip() + "\n")
    print(message)


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    log("+ " + " ".join(cmd))
    return subprocess.run(cmd, text=True, capture_output=True, check=check)


def load_meta() -> dict[str, Any]:
    if not META_FILE.exists():
        log(f"No AtlasVM network metadata file found: {META_FILE}")
        return {}

    try:
        return json.loads(META_FILE.read_text())
    except Exception as exc:
        raise RuntimeError(f"Could not read {META_FILE}: {exc}") from exc


def link_exists(name: str) -> bool:
    return run(["ip", "link", "show", "dev", name], check=False).returncode == 0


def set_link_up(name: str) -> None:
    run(["ip", "link", "set", name, "up"])


def ensure_bridge(bridge_name: str) -> None:
    if not bridge_name:
        raise RuntimeError("Bridge name is required.")

    if not link_exists(bridge_name):
        run(["ip", "link", "add", "name", bridge_name, "type", "bridge"])

    set_link_up(bridge_name)


def ensure_parent_to_bridge(parent_interface: str, bridge_name: str) -> None:
    if not parent_interface:
        raise RuntimeError("Parent interface is required.")

    if not link_exists(parent_interface):
        raise RuntimeError(f"Parent interface does not exist: {parent_interface}")

    ensure_bridge(bridge_name)

    # If already enslaved correctly, this is harmless enough. If not, ip will do the thing or complain.
    run(["ip", "link", "set", parent_interface, "master", bridge_name])
    set_link_up(parent_interface)
    set_link_up(bridge_name)


def validate_vlan(vlan_tag: str | int | None) -> str:
    vlan_tag = str(vlan_tag or "").strip()
    if not vlan_tag:
        return ""

    vlan_id = int(vlan_tag)
    if vlan_id < 1 or vlan_id > 4094:
        raise RuntimeError(f"Invalid VLAN tag: {vlan_tag}")

    return str(vlan_id)


def short_vlan_ifname(parent_interface: str, vlan_tag: str) -> str:
    candidate = f"{parent_interface}.{vlan_tag}"
    if len(candidate) <= 15:
        return candidate

    # Linux interface names are 15 chars max, because joy has limits.
    compact = "".join(ch for ch in parent_interface if ch.isalnum())
    return f"v{vlan_tag}{compact}"[:15]


def ensure_vlan_bridge(parent_interface: str, vlan_tag: str, bridge_name: str, existing_vlan_if: str = "") -> str:
    vlan_tag = validate_vlan(vlan_tag)

    if not parent_interface:
        raise RuntimeError("Parent interface is required for VLAN bridge.")

    if not link_exists(parent_interface):
        raise RuntimeError(f"Parent interface does not exist: {parent_interface}")

    vlan_if = existing_vlan_if.strip() or short_vlan_ifname(parent_interface, vlan_tag)

    # A VLAN interface for this parent/tag may already exist under a different name.
    if not link_exists(vlan_if):
        found = find_vlan_interface(parent_interface, vlan_tag)
        if found:
            vlan_if = found

    if not link_exists(vlan_if):
        run(["ip", "link", "add", "link", parent_interface, "name", vlan_if, "type", "vlan", "id", vlan_tag])

    ensure_bridge(bridge_name)

    run(["ip", "link", "set", vlan_if, "master", bridge_name])
    set_link_up(parent_interface)
    set_link_up(vlan_if)
    set_link_up(bridge_name)

    return vlan_if


def find_vlan_interface(parent_interface: str, vlan_tag: str) -> str:
    result = run(["ip", "-d", "-j", "link", "show", "type", "vlan"], check=False)
    if result.returncode != 0:
        return ""

    try:
        links = json.loads(result.stdout)
    except Exception:
        return ""

    for link in links:
        name = link.get("ifname", "")
        linkinfo = link.get("linkinfo", {})
        info_data = linkinfo.get("info_data", {})

        parent = link.get("link", "")
        vlan_id = str(info_data.get("id", ""))

        if parent == parent_interface and vlan_id == str(vlan_tag):
            return name

    return ""


def libvirt_network_exists(name: str) -> bool:
    conn = libvirt.open(LIBVIRT_URI)
    if conn is None:
        raise RuntimeError("Could not connect to libvirt.")

    try:
        try:
            conn.networkLookupByName(name)
            return True
        except libvirt.libvirtError:
            return False
    finally:
        conn.close()


def start_libvirt_network(name: str) -> None:
    conn = libvirt.open(LIBVIRT_URI)
    if conn is None:
        raise RuntimeError("Could not connect to libvirt.")

    try:
        net = conn.networkLookupByName(name)
        if not net.isActive():
            log(f"Starting libvirt network: {name}")
            net.create()
    finally:
        conn.close()


def reconcile_network(name: str, data: dict[str, Any]) -> dict[str, Any]:
    network_type = str(data.get("type") or "").strip()
    parent_interface = str(data.get("parent_interface") or "").strip()
    bridge_name = str(data.get("bridge") or "").strip()
    vlan_tag = str(data.get("vlan_tag") or "").strip()
    vlan_interface = str(data.get("vlan_interface") or "").strip()

    log(f"Reconciling network {name}: type={network_type}, bridge={bridge_name}, parent={parent_interface}, vlan={vlan_tag}")

    if network_type == "vlan_bridge":
        vlan_interface = ensure_vlan_bridge(parent_interface, vlan_tag, bridge_name, vlan_interface)
        data["vlan_interface"] = vlan_interface

    elif network_type == "bridge":
        ensure_parent_to_bridge(parent_interface, bridge_name)

    elif network_type in {"nat", "isolated"}:
        log(f"Network {name} is {network_type}; libvirt handles runtime bridge/NAT plumbing.")

    else:
        log(f"Skipping {name}: unknown or missing type: {network_type}")
        return data

    if data.get("autostart") or data.get("start") or data.get("active"):
        if libvirt_network_exists(name):
            start_libvirt_network(name)
        else:
            log(f"Libvirt network does not exist, cannot start: {name}")

    return data


def reconcile_all() -> None:
    log("=== AtlasVM network reconcile starting ===")

    meta = load_meta()
    changed = False

    for name, data in list(meta.items()):
        try:
            updated = reconcile_network(name, data)
            if updated != data:
                meta[name] = updated
                changed = True
        except Exception as exc:
            log(f"ERROR reconciling {name}: {exc}")

    if changed:
        META_FILE.write_text(json.dumps(meta, indent=2, sort_keys=True))
        log(f"Updated metadata file: {META_FILE}")

    log("=== AtlasVM network reconcile complete ===")


if __name__ == "__main__":
    reconcile_all()
