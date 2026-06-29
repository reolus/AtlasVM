from __future__ import annotations

import ipaddress
import json
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import libvirt


META_FILE = Path("/opt/atlasvm/atlasvm_networks.json")
HOST_META_FILE = Path("/opt/atlasvm/atlasvm_host_network.json")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True))


def _run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, text=True, capture_output=True, check=check)


def _safe_name(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip())
    value = value.strip(".-_")
    if not value:
        raise RuntimeError("Name is required.")
    if len(value) > 63:
        raise RuntimeError("Name must be 63 characters or fewer.")
    return value


def _linux_ifname(prefix: str, name: str) -> str:
    raw = re.sub(r"[^A-Za-z0-9]+", "", name.lower())
    if not raw:
        raw = "net"
    return (prefix + raw)[:15]


def _validate_vlan(vlan_tag: str | None) -> str:
    vlan_tag = str(vlan_tag or "").strip()
    if not vlan_tag:
        return ""
    vlan = int(vlan_tag)
    if vlan < 1 or vlan > 4094:
        raise RuntimeError("VLAN tag must be between 1 and 4094.")
    return str(vlan)


def _cidr_parts(cidr: str | None) -> tuple[str, str, str] | None:
    cidr = str(cidr or "").strip()
    if not cidr:
        return None
    net = ipaddress.ip_network(cidr, strict=False)
    hosts = list(net.hosts())
    if not hosts:
        raise RuntimeError("CIDR does not contain usable host addresses.")
    gateway = str(hosts[0])
    return str(net), gateway, str(net.netmask)


class NetworkPhase8Service:
    def __init__(self, uri: str = "qemu:///system"):
        self.uri = uri

    def _conn(self):
        conn = libvirt.open(self.uri)
        if conn is None:
            raise RuntimeError("Could not connect to libvirt.")
        return conn

    def load_meta(self) -> dict[str, Any]:
        return _read_json(META_FILE)

    def save_meta(self, meta: dict[str, Any]) -> None:
        _write_json(META_FILE, meta)

    def get_meta(self, name: str) -> dict[str, Any]:
        return self.load_meta().get(name, {})

    def set_meta(self, name: str, data: dict[str, Any]) -> None:
        meta = self.load_meta()
        current = meta.get(name, {})
        current.update(data)
        meta[name] = current
        self.save_meta(meta)

    def remove_meta(self, name: str) -> None:
        meta = self.load_meta()
        if name in meta:
            del meta[name]
            self.save_meta(meta)

    def get_vlan_tag(self, name: str) -> str:
        return str(self.get_meta(name).get("vlan_tag") or "").strip()

    def list_host_links(self) -> list[dict[str, Any]]:
        try:
            links = json.loads(_run(["ip", "-j", "link", "show"]).stdout)
        except Exception:
            links = []

        try:
            addrs = json.loads(_run(["ip", "-j", "addr", "show"]).stdout)
        except Exception:
            addrs = []

        addr_by_name = {item.get("ifname"): item for item in addrs}
        result = []

        for link in links:
            name = link.get("ifname")
            if not name:
                continue

            addr_info = addr_by_name.get(name, {}).get("addr_info", [])
            addresses = []
            for addr in addr_info:
                local = addr.get("local")
                prefix = addr.get("prefixlen")
                family = addr.get("family")
                if local and prefix is not None:
                    addresses.append(f"{local}/{prefix} ({family})")

            result.append({
                "name": name,
                "state": link.get("operstate", ""),
                "mac": link.get("address", ""),
                "type": link.get("link_type", ""),
                "master": link.get("master", ""),
                "addresses": addresses,
            })

        return result

    def list_routes(self) -> list[dict[str, Any]]:
        try:
            return json.loads(_run(["ip", "-j", "route", "show"]).stdout)
        except Exception:
            return []

    def _link_exists(self, name: str) -> bool:
        return _run(["ip", "link", "show", "dev", name], check=False).returncode == 0

    def _ensure_link_up(self, name: str) -> None:
        _run(["ip", "link", "set", name, "up"])

    def _ensure_bridge(self, bridge_name: str) -> None:
        if not self._link_exists(bridge_name):
            _run(["ip", "link", "add", "name", bridge_name, "type", "bridge"])
        self._ensure_link_up(bridge_name)

    def _ensure_parent_to_bridge(self, parent_interface: str, bridge_name: str) -> None:
        if not self._link_exists(parent_interface):
            raise RuntimeError(f"Parent interface does not exist: {parent_interface}")
        self._ensure_bridge(bridge_name)
        _run(["ip", "link", "set", parent_interface, "master", bridge_name])
        self._ensure_link_up(parent_interface)
        self._ensure_link_up(bridge_name)

    def _ensure_vlan_bridge(self, parent_interface: str, vlan_tag: str, bridge_name: str) -> str:
        vlan_tag = _validate_vlan(vlan_tag)
        if not vlan_tag:
            raise RuntimeError("VLAN tag is required for a VLAN bridge network.")

        if not self._link_exists(parent_interface):
            raise RuntimeError(f"Parent interface does not exist: {parent_interface}")

        vlan_if = f"{parent_interface}.{vlan_tag}"
        if len(vlan_if) > 15:
            vlan_if = _linux_ifname("v", f"{parent_interface}{vlan_tag}")

        if not self._link_exists(vlan_if):
            _run(["ip", "link", "add", "link", parent_interface, "name", vlan_if, "type", "vlan", "id", vlan_tag])

        self._ensure_bridge(bridge_name)
        _run(["ip", "link", "set", vlan_if, "master", bridge_name])
        self._ensure_link_up(parent_interface)
        self._ensure_link_up(vlan_if)
        self._ensure_link_up(bridge_name)
        return vlan_if

    def _network_xml(
        self,
        name: str,
        network_type: str,
        bridge_name: str,
        cidr: str = "",
        dhcp_start: str = "",
        dhcp_end: str = "",
        domain_name: str = "",
    ) -> str:
        network = ET.Element("network")
        ET.SubElement(network, "name").text = name

        if network_type == "nat":
            ET.SubElement(network, "forward", {"mode": "nat"})
            if bridge_name:
                ET.SubElement(network, "bridge", {"name": bridge_name})
        elif network_type == "isolated":
            if bridge_name:
                ET.SubElement(network, "bridge", {"name": bridge_name})
        elif network_type in {"bridge", "vlan_bridge"}:
            ET.SubElement(network, "forward", {"mode": "bridge"})
            ET.SubElement(network, "bridge", {"name": bridge_name})
        else:
            raise RuntimeError(f"Unsupported network type: {network_type}")

        if domain_name:
            ET.SubElement(network, "domain", {"name": domain_name})

        cidr_info = _cidr_parts(cidr)
        if cidr_info:
            _net, gateway, netmask = cidr_info
            ip_el = ET.SubElement(network, "ip", {"address": gateway, "netmask": netmask})
            if dhcp_start and dhcp_end:
                dhcp_el = ET.SubElement(ip_el, "dhcp")
                ET.SubElement(dhcp_el, "range", {"start": dhcp_start, "end": dhcp_end})

        return ET.tostring(network, encoding="unicode")

    def create_network(
        self,
        name: str,
        network_type: str,
        parent_interface: str = "",
        bridge_name: str = "",
        vlan_tag: str = "",
        cidr: str = "",
        dhcp_start: str = "",
        dhcp_end: str = "",
        domain_name: str = "",
        autostart: bool = False,
        start: bool = False,
    ) -> dict[str, Any]:
        name = _safe_name(name)
        network_type = (network_type or "nat").strip()
        vlan_tag = _validate_vlan(vlan_tag)
        parent_interface = str(parent_interface or "").strip()
        bridge_name = str(bridge_name or "").strip()

        if network_type not in {"nat", "isolated", "bridge", "vlan_bridge"}:
            raise RuntimeError("Network type must be nat, isolated, bridge, or vlan_bridge.")

        if network_type == "nat" and not cidr:
            raise RuntimeError("CIDR is required for NAT networks.")

        if network_type in {"bridge", "vlan_bridge"}:
            if not parent_interface:
                raise RuntimeError("Parent interface is required for bridge-backed networks.")
            if not bridge_name:
                bridge_name = _linux_ifname("br", name)

        if network_type == "vlan_bridge":
            vlan_if = self._ensure_vlan_bridge(parent_interface, vlan_tag, bridge_name)
        elif network_type == "bridge":
            vlan_if = ""
            self._ensure_parent_to_bridge(parent_interface, bridge_name)
        else:
            vlan_if = ""
            if not bridge_name:
                bridge_name = _linux_ifname("virbr", name)

        xml = self._network_xml(
            name=name,
            network_type=network_type,
            bridge_name=bridge_name,
            cidr=cidr,
            dhcp_start=dhcp_start,
            dhcp_end=dhcp_end,
            domain_name=domain_name,
        )

        conn = self._conn()
        try:
            try:
                conn.networkLookupByName(name)
                raise RuntimeError(f"Network already exists: {name}")
            except libvirt.libvirtError:
                pass

            net = conn.networkDefineXML(xml)
            if autostart:
                net.setAutostart(1)
            if start:
                net.create()
        finally:
            conn.close()

        data = {
            "name": name,
            "type": network_type,
            "parent_interface": parent_interface,
            "bridge": bridge_name,
            "vlan_tag": vlan_tag,
            "vlan_interface": vlan_if,
            "cidr": cidr,
            "dhcp_start": dhcp_start,
            "dhcp_end": dhcp_end,
            "domain_name": domain_name,
        }
        self.set_meta(name, data)
        return data

    def update_network_meta(
        self,
        name: str,
        vlan_tag: str = "",
        description: str = "",
        parent_interface: str = "",
        bridge_name: str = "",
    ) -> None:
        vlan_tag = _validate_vlan(vlan_tag)
        conn = self._conn()
        try:
            conn.networkLookupByName(name)
        finally:
            conn.close()

        current = self.get_meta(name)
        current["vlan_tag"] = vlan_tag
        current["description"] = description
        if parent_interface:
            current["parent_interface"] = parent_interface
        if bridge_name:
            current["bridge"] = bridge_name
        if vlan_tag and current.get("type") == "vlan_bridge":
            parent = current.get("parent_interface") or parent_interface
            bridge = current.get("bridge") or bridge_name
            if parent and bridge:
                current["vlan_interface"] = self._ensure_vlan_bridge(parent, vlan_tag, bridge)
        self.set_meta(name, current)

    def _libvirt_network_summary(self, net) -> dict[str, Any]:
        xml = net.XMLDesc(0)
        root = ET.fromstring(xml)
        bridge_el = root.find("bridge")
        forward_el = root.find("forward")
        ip_el = root.find("ip")

        bridge = bridge_el.attrib.get("name", "") if bridge_el is not None else ""
        forward = forward_el.attrib.get("mode", "") if forward_el is not None else ""
        cidr = ""
        if ip_el is not None:
            address = ip_el.attrib.get("address")
            netmask = ip_el.attrib.get("netmask")
            if address and netmask:
                try:
                    cidr = str(ipaddress.ip_network(f"{address}/{netmask}", strict=False))
                except Exception:
                    cidr = f"{address}/{netmask}"

        return {
            "bridge": bridge,
            "forward": forward,
            "cidr": cidr,
            "xml": xml,
        }

    def list_networks(self) -> list[dict[str, Any]]:
        meta = self.load_meta()
        result = {}
        conn = self._conn()
        try:
            for name in conn.listNetworks():
                net = conn.networkLookupByName(name)
                summary = self._libvirt_network_summary(net)
                item = {
                    "name": name,
                    "active": True,
                    "autostart": bool(net.autostart()),
                    **summary,
                    **meta.get(name, {}),
                }
                item["vlan_tag"] = str(item.get("vlan_tag") or "")
                item["type"] = item.get("type") or self._infer_type(item)
                result[name] = item

            for name in conn.listDefinedNetworks():
                net = conn.networkLookupByName(name)
                summary = self._libvirt_network_summary(net)
                item = {
                    "name": name,
                    "active": False,
                    "autostart": bool(net.autostart()),
                    **summary,
                    **meta.get(name, {}),
                }
                item["vlan_tag"] = str(item.get("vlan_tag") or "")
                item["type"] = item.get("type") or self._infer_type(item)
                result[name] = item
        finally:
            conn.close()

        return sorted(result.values(), key=lambda x: x["name"])

    def _infer_type(self, item: dict[str, Any]) -> str:
        if item.get("vlan_tag"):
            return "vlan_bridge"
        if item.get("forward") == "bridge":
            return "bridge"
        if item.get("forward") == "nat":
            return "nat"
        return "isolated"

    def get_network(self, name: str) -> dict[str, Any]:
        conn = self._conn()
        try:
            net = conn.networkLookupByName(name)
            summary = self._libvirt_network_summary(net)
            item = {
                "name": name,
                "active": bool(net.isActive()),
                "autostart": bool(net.autostart()),
                **summary,
                **self.get_meta(name),
            }
            item["vlan_tag"] = str(item.get("vlan_tag") or "")
            item["type"] = item.get("type") or self._infer_type(item)
            return item
        finally:
            conn.close()

    def attached_vms(self, name: str) -> list[dict[str, Any]]:
        attached: dict[str, dict[str, Any]] = {}
        conn = self._conn()
        try:
            domains = []

            for dom_id in conn.listDomainsID():
                domains.append((conn.lookupByID(dom_id), "running", 0))

            for dom_name in conn.listDefinedDomains():
                domains.append((conn.lookupByName(dom_name), "shutoff", libvirt.VIR_DOMAIN_XML_INACTIVE))

            for dom, state, flags in domains:
                root = ET.fromstring(dom.XMLDesc(flags))
                interfaces = []

                for iface in root.findall("./devices/interface"):
                    source = iface.find("source")
                    if source is None or source.attrib.get("network") != name:
                        continue

                    mac = iface.find("mac")
                    model = iface.find("model")

                    interfaces.append({
                        "mac": mac.attrib.get("address") if mac is not None else "",
                        "model": model.attrib.get("type") if model is not None else "",
                    })

                if interfaces:
                    attached[dom.name()] = {
                        "name": dom.name(),
                        "state": state,
                        "interfaces": interfaces,
                    }
        finally:
            conn.close()

        return sorted(attached.values(), key=lambda x: x["name"])

    def action(self, name: str, action: str) -> None:
        conn = self._conn()
        try:
            net = conn.networkLookupByName(name)

            if action == "start":
                if not net.isActive():
                    meta = self.get_meta(name)
                    if meta.get("type") == "vlan_bridge":
                        self._ensure_vlan_bridge(meta.get("parent_interface", ""), meta.get("vlan_tag", ""), meta.get("bridge", ""))
                    elif meta.get("type") == "bridge" and meta.get("parent_interface") and meta.get("bridge"):
                        self._ensure_parent_to_bridge(meta["parent_interface"], meta["bridge"])
                    net.create()
            elif action == "stop":
                if net.isActive():
                    net.destroy()
            elif action == "autostart-enable":
                net.setAutostart(1)
            elif action == "autostart-disable":
                net.setAutostart(0)
            else:
                raise RuntimeError(f"Unsupported network action: {action}")
        finally:
            conn.close()

    def delete_network(self, name: str) -> None:
        meta = self.get_meta(name)
        conn = self._conn()
        try:
            net = conn.networkLookupByName(name)
            if net.isActive():
                net.destroy()
            net.undefine()
        finally:
            conn.close()

        self.remove_meta(name)

        bridge = meta.get("bridge")
        vlan_if = meta.get("vlan_interface")

        if bridge and self._link_exists(bridge):
            _run(["ip", "link", "delete", bridge], check=False)

        if vlan_if and self._link_exists(vlan_if):
            _run(["ip", "link", "delete", vlan_if], check=False)

    def host_management_meta(self) -> dict[str, Any]:
        return _read_json(HOST_META_FILE)

    def save_host_management_meta(
        self,
        management_interface: str = "",
        vlan_tag: str = "",
        static_ip: str = "",
        subnet: str = "",
        gateway: str = "",
        dns_servers: str = "",
    ) -> None:
        data = {
            "management_interface": management_interface.strip(),
            "vlan_tag": _validate_vlan(vlan_tag),
            "static_ip": static_ip.strip(),
            "subnet": subnet.strip(),
            "gateway": gateway.strip(),
            "dns_servers": dns_servers.strip(),
            "note": "Saved only. AtlasVM does not rewrite host management networking until an apply-with-rollback step is implemented.",
        }
        _write_json(HOST_META_FILE, data)
