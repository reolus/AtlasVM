from __future__ import annotations

import json
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import libvirt


def run(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, text=True, capture_output=True, check=check)


def validate_vm_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise RuntimeError("VM name is required.")
    return name


def validate_model_type(model_type: str) -> str:
    model_type = (model_type or "virtio").strip()
    allowed = {"virtio", "e1000e", "e1000", "rtl8139"}
    if model_type not in allowed:
        raise RuntimeError(f"Unsupported NIC model: {model_type}")
    return model_type


def normalize_mac(mac: str) -> str:
    return (mac or "").strip().lower()


def network_vlan_tag(network_name: str, libvirt_uri: str = "qemu:///system") -> str:
    network_name = (network_name or "").strip()
    if not network_name:
        return ""

    metadata_paths = [
        Path("/opt/atlasvm/atlasvm_networks.json"),
        Path("/opt/atlasvm/atlasvm_network_meta.json"),
    ]

    for path in metadata_paths:
        try:
            if not path.exists():
                continue

            data = json.loads(path.read_text() or "{}")
            item = data.get(network_name) or {}

            for key in ("vlan_tag", "vlan", "tag"):
                value = item.get(key)
                if value not in (None, ""):
                    return str(value)
        except Exception:
            pass

    try:
        result = run(["virsh", "net-dumpxml", network_name], check=False)
        if result.returncode != 0:
            return ""

        root = ET.fromstring(result.stdout)

        tag = root.find("./vlan/tag")
        if tag is not None and tag.get("id"):
            return str(tag.get("id"))

        bridge = root.find("bridge")
        if bridge is not None:
            bridge_name = bridge.get("name", "")
            match = re.search(r"(\d{1,4})$", bridge_name)
            if match:
                return match.group(1)
    except Exception:
        return ""

    return ""


def list_libvirt_networks(libvirt_uri: str) -> list[dict[str, Any]]:
    conn = libvirt.open(libvirt_uri)
    if conn is None:
        raise RuntimeError("Could not connect to libvirt.")

    try:
        names: set[str] = set(conn.listNetworks() or [])
        defined: set[str] = set(conn.listDefinedNetworks() or [])

        networks = []
        for name in sorted(names | defined, key=lambda value: value.lower()):
            active = name in names
            vlan = network_vlan_tag(name, libvirt_uri)

            networks.append({
                "name": name,
                "active": active,
                "vlan_tag": vlan,
                "label": f"{name} - VLAN {vlan}" if vlan else f"{name} - untagged",
            })

        return networks
    finally:
        conn.close()


def vm_is_running(vm_name: str, libvirt_uri: str) -> bool:
    conn = libvirt.open(libvirt_uri)
    if conn is None:
        raise RuntimeError("Could not connect to libvirt.")

    try:
        dom = conn.lookupByName(vm_name)
        return bool(dom.isActive())
    finally:
        conn.close()


def read_vm_interfaces(vm_name: str, libvirt_uri: str) -> list[dict[str, Any]]:
    vm_name = validate_vm_name(vm_name)

    conn = libvirt.open(libvirt_uri)
    if conn is None:
        raise RuntimeError("Could not connect to libvirt.")

    try:
        dom = conn.lookupByName(vm_name)
        is_active = bool(dom.isActive())

        # For the page, inactive XML is usually the best config view.
        # If inactive XML is unavailable for a running VM, fall back to live XML.
        try:
            xml = dom.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE)
        except Exception:
            xml = dom.XMLDesc(0 if is_active else libvirt.VIR_DOMAIN_XML_INACTIVE)

        root = ET.fromstring(xml)
        interfaces = []

        for idx, iface in enumerate(root.findall("./devices/interface"), start=1):
            iface_type = iface.get("type", "")
            mac_el = iface.find("mac")
            source_el = iface.find("source")
            model_el = iface.find("model")

            mac = mac_el.get("address", "") if mac_el is not None else ""
            model = model_el.get("type", "") if model_el is not None else ""

            source = ""
            source_kind = ""

            if source_el is not None:
                for attr in ("network", "bridge", "dev"):
                    value = source_el.get(attr)
                    if value:
                        source = value
                        source_kind = attr
                        break

            vm_vlan = ""
            vlan_el = iface.find("vlan")
            if vlan_el is not None:
                tag_el = vlan_el.find("tag")
                if tag_el is not None:
                    vm_vlan = tag_el.get("id", "")

            inherited_vlan = network_vlan_tag(source, libvirt_uri) if source_kind == "network" else ""

            interfaces.append({
                "index": idx,
                "type": iface_type,
                "mac": mac,
                "source": source,
                "source_kind": source_kind,
                "model": model or "virtio",
                "vm_vlan_tag": vm_vlan,
                "network_vlan_tag": inherited_vlan,
                "effective_vlan_tag": inherited_vlan or vm_vlan,
                "warning": "VM NIC has its own VLAN tag. AtlasVM expects VLAN tagging on the network/bridge, not inside the VM NIC." if vm_vlan else "",
            })

        return interfaces
    finally:
        conn.close()


def build_interface_xml(network_name: str, mac: str = "", model_type: str = "virtio") -> str:
    network_name = (network_name or "").strip()
    if not network_name:
        raise RuntimeError("Network name is required.")

    model_type = validate_model_type(model_type)

    iface = ET.Element("interface", {"type": "network"})

    mac = normalize_mac(mac)
    if mac:
        ET.SubElement(iface, "mac", {"address": mac})

    ET.SubElement(iface, "source", {"network": network_name})
    ET.SubElement(iface, "model", {"type": model_type})

    # Important:
    # Do NOT add <vlan> here. AtlasVM owns VLAN tagging at the host bridge /
    # libvirt network layer. Adding VLAN here double-tags traffic.
    return ET.tostring(iface, encoding="unicode")


def find_interface_by_mac(root: ET.Element, mac_address: str) -> ET.Element:
    mac_address = normalize_mac(mac_address)
    if not mac_address:
        raise RuntimeError("MAC address is required.")

    for iface in root.findall("./devices/interface"):
        mac_el = iface.find("mac")
        if mac_el is not None and normalize_mac(mac_el.get("address", "")) == mac_address:
            return iface

    raise RuntimeError(f"No interface found with MAC address {mac_address}.")


def ensure_network_exists(conn: libvirt.virConnect, network_name: str) -> None:
    try:
        conn.networkLookupByName(network_name)
    except Exception:
        raise RuntimeError(f"Network does not exist: {network_name}")


def update_vm_interface(
    vm_name: str,
    network_name: str,
    mac_address: str,
    live_switch: bool,
    libvirt_uri: str,
) -> dict[str, Any]:
    vm_name = validate_vm_name(vm_name)
    network_name = (network_name or "").strip()
    mac_address = normalize_mac(mac_address)

    if not network_name:
        raise RuntimeError("Network name is required.")

    if not mac_address:
        raise RuntimeError("MAC address is required.")

    conn = libvirt.open(libvirt_uri)
    if conn is None:
        raise RuntimeError("Could not connect to libvirt.")

    try:
        ensure_network_exists(conn, network_name)

        dom = conn.lookupByName(vm_name)
        is_active = bool(dom.isActive())

        if is_active and not live_switch:
            raise RuntimeError('VM is running. Check "Apply live" to update this NIC now, or shut down the VM first.')

        xml_flags = 0 if is_active else libvirt.VIR_DOMAIN_XML_INACTIVE
        xml = dom.XMLDesc(xml_flags)
        root = ET.fromstring(xml)

        selected = find_interface_by_mac(root, mac_address)

        model_el = selected.find("model")
        model_type = model_el.get("type") if model_el is not None else "virtio"
        model_type = validate_model_type(model_type or "virtio")

        old_iface_xml = ET.tostring(selected, encoding="unicode")
        new_iface_xml = build_interface_xml(network_name=network_name, mac=mac_address, model_type=model_type)

        if is_active:
            flags = libvirt.VIR_DOMAIN_AFFECT_LIVE | libvirt.VIR_DOMAIN_AFFECT_CONFIG

            try:
                dom.detachDeviceFlags(old_iface_xml, flags)
            except Exception as exc:
                raise RuntimeError(f"Failed to detach existing NIC: {exc}")

            try:
                dom.attachDeviceFlags(new_iface_xml, flags)
            except Exception as exc:
                try:
                    dom.attachDeviceFlags(old_iface_xml, flags)
                except Exception:
                    pass
                raise RuntimeError(f"Failed to attach updated NIC: {exc}")

            mode = "live+config"
        else:
            flags = libvirt.VIR_DOMAIN_AFFECT_CONFIG
            try:
                dom.detachDeviceFlags(old_iface_xml, flags)
                dom.attachDeviceFlags(new_iface_xml, flags)
            except Exception as exc:
                try:
                    dom.attachDeviceFlags(old_iface_xml, flags)
                except Exception:
                    pass
                raise RuntimeError(f"Failed to update NIC configuration: {exc}")
            mode = "config"

        return {
            "vm": vm_name,
            "network": network_name,
            "mac": mac_address,
            "mode": mode,
            "vlan_tag": network_vlan_tag(network_name, libvirt_uri),
        }
    finally:
        conn.close()


def add_vm_interface(
    vm_name: str,
    network_name: str,
    model_type: str,
    apply_live: bool,
    libvirt_uri: str,
) -> dict[str, Any]:
    vm_name = validate_vm_name(vm_name)
    network_name = (network_name or "").strip()
    model_type = validate_model_type(model_type)

    if not network_name:
        raise RuntimeError("Network name is required.")

    conn = libvirt.open(libvirt_uri)
    if conn is None:
        raise RuntimeError("Could not connect to libvirt.")

    try:
        ensure_network_exists(conn, network_name)

        dom = conn.lookupByName(vm_name)
        is_active = bool(dom.isActive())

        if is_active and not apply_live:
            raise RuntimeError('VM is running. Check "Apply live" to add this NIC now, or shut down the VM first.')

        new_iface_xml = build_interface_xml(network_name=network_name, model_type=model_type)

        if is_active:
            flags = libvirt.VIR_DOMAIN_AFFECT_LIVE | libvirt.VIR_DOMAIN_AFFECT_CONFIG
            dom.attachDeviceFlags(new_iface_xml, flags)
            mode = "live+config"
        else:
            flags = libvirt.VIR_DOMAIN_AFFECT_CONFIG
            dom.attachDeviceFlags(new_iface_xml, flags)
            mode = "config"

        return {
            "vm": vm_name,
            "network": network_name,
            "mode": mode,
            "vlan_tag": network_vlan_tag(network_name, libvirt_uri),
        }
    finally:
        conn.close()


def remove_vm_interface(
    vm_name: str,
    mac_address: str,
    apply_live: bool,
    libvirt_uri: str,
) -> dict[str, Any]:
    vm_name = validate_vm_name(vm_name)
    mac_address = normalize_mac(mac_address)

    if not mac_address:
        raise RuntimeError("MAC address is required.")

    conn = libvirt.open(libvirt_uri)
    if conn is None:
        raise RuntimeError("Could not connect to libvirt.")

    try:
        dom = conn.lookupByName(vm_name)
        is_active = bool(dom.isActive())

        if is_active and not apply_live:
            raise RuntimeError('VM is running. Check "Apply live" to remove this NIC now, or shut down the VM first.')

        xml_flags = 0 if is_active else libvirt.VIR_DOMAIN_XML_INACTIVE
        xml = dom.XMLDesc(xml_flags)
        root = ET.fromstring(xml)

        selected = find_interface_by_mac(root, mac_address)
        old_iface_xml = ET.tostring(selected, encoding="unicode")

        if is_active:
            flags = libvirt.VIR_DOMAIN_AFFECT_LIVE | libvirt.VIR_DOMAIN_AFFECT_CONFIG
            dom.detachDeviceFlags(old_iface_xml, flags)
            mode = "live+config"
        else:
            flags = libvirt.VIR_DOMAIN_AFFECT_CONFIG
            dom.detachDeviceFlags(old_iface_xml, flags)
            mode = "config"

        return {
            "vm": vm_name,
            "mac": mac_address,
            "mode": mode,
        }
    finally:
        conn.close()


def remove_vm_interface_vlan_tag(
    vm_name: str,
    mac_address: str,
    apply_live: bool,
    libvirt_uri: str,
) -> dict[str, Any]:
    """
    Remove a VM-side VLAN tag from a NIC.

    AtlasVM expects VLAN tagging to happen at the host/libvirt network layer.
    This removes only <vlan> from the VM interface XML.
    """
    vm_name = validate_vm_name(vm_name)
    mac_address = normalize_mac(mac_address)

    if not mac_address:
        raise RuntimeError("MAC address is required.")

    conn = libvirt.open(libvirt_uri)
    if conn is None:
        raise RuntimeError("Could not connect to libvirt.")

    try:
        dom = conn.lookupByName(vm_name)
        is_active = bool(dom.isActive())

        if is_active and not apply_live:
            raise RuntimeError('VM is running. Check "Apply live" to clean this NIC now, or shut down the VM first.')

        xml_flags = 0 if is_active else libvirt.VIR_DOMAIN_XML_INACTIVE
        xml = dom.XMLDesc(xml_flags)
        root = ET.fromstring(xml)

        selected = find_interface_by_mac(root, mac_address)
        vlan_el = selected.find("vlan")

        if vlan_el is None:
            return {
                "vm": vm_name,
                "mac": mac_address,
                "mode": "none",
                "message": "NIC does not have a VM-side VLAN tag.",
            }

        old_iface_xml = ET.tostring(selected, encoding="unicode")

        clean_iface = ET.fromstring(old_iface_xml)
        clean_vlan = clean_iface.find("vlan")
        if clean_vlan is not None:
            clean_iface.remove(clean_vlan)

        clean_iface_xml = ET.tostring(clean_iface, encoding="unicode")

        if is_active:
            flags = libvirt.VIR_DOMAIN_AFFECT_LIVE | libvirt.VIR_DOMAIN_AFFECT_CONFIG

            try:
                dom.detachDeviceFlags(old_iface_xml, flags)
            except Exception as exc:
                raise RuntimeError(f"Failed to detach VLAN-tagged NIC: {exc}")

            try:
                dom.attachDeviceFlags(clean_iface_xml, flags)
            except Exception as exc:
                try:
                    dom.attachDeviceFlags(old_iface_xml, flags)
                except Exception:
                    pass
                raise RuntimeError(f"Failed to attach cleaned NIC. Original NIC rollback was attempted. Error: {exc}")

            mode = "live+config"
        else:
            flags = libvirt.VIR_DOMAIN_AFFECT_CONFIG
            try:
                dom.detachDeviceFlags(old_iface_xml, flags)
                dom.attachDeviceFlags(clean_iface_xml, flags)
            except Exception as exc:
                try:
                    dom.attachDeviceFlags(old_iface_xml, flags)
                except Exception:
                    pass
                raise RuntimeError(f"Failed to clean NIC configuration: {exc}")
            mode = "config"

        return {
            "vm": vm_name,
            "mac": mac_address,
            "mode": mode,
            "message": f"Removed VM-side VLAN tag from NIC {mac_address} using {mode}.",
        }
    finally:
        conn.close()
