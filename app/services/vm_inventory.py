from __future__ import annotations

import json
import subprocess
import xml.etree.ElementTree as ET
from typing import Any


def run(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, text=True, capture_output=True, check=check)


def split_lines(value: str) -> list[str]:
    return [line.strip() for line in (value or "").splitlines() if line.strip()]


def virsh_xml(vm_name: str) -> str:
    result = run(["virsh", "dumpxml", vm_name], check=False)
    if result.returncode != 0:
        return ""
    return result.stdout


def parse_vm_xml(vm_name: str, xml_text: str) -> dict[str, Any]:
    info: dict[str, Any] = {
        "name": vm_name,
        "uuid": "",
        "memory": "",
        "current_memory": "",
        "vcpu": "",
        "os_type": "",
        "disks": [],
        "interfaces": [],
        "graphics": [],
        "autostart": False,
    }

    if not xml_text:
        return info

    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return info

    info["uuid"] = root.findtext("uuid") or ""
    info["memory"] = root.findtext("memory") or ""
    info["current_memory"] = root.findtext("currentMemory") or ""
    info["vcpu"] = root.findtext("vcpu") or ""
    info["os_type"] = root.findtext("./os/type") or ""

    devices = root.find("devices")
    if devices is not None:
        for disk in devices.findall("disk"):
            if disk.get("device") not in {"disk", "cdrom"}:
                continue

            source = disk.find("source")
            target = disk.find("target")
            driver = disk.find("driver")

            source_value = ""
            if source is not None:
                source_value = (
                    source.get("file")
                    or source.get("dev")
                    or source.get("name")
                    or source.get("volume")
                    or ""
                )

            info["disks"].append({
                "device": disk.get("device", ""),
                "type": disk.get("type", ""),
                "source": source_value,
                "target": target.get("dev", "") if target is not None else "",
                "bus": target.get("bus", "") if target is not None else "",
                "format": driver.get("type", "") if driver is not None else "",
            })

        for iface in devices.findall("interface"):
            source = iface.find("source")
            model = iface.find("model")
            mac = iface.find("mac")

            source_value = ""
            if source is not None:
                source_value = (
                    source.get("network")
                    or source.get("bridge")
                    or source.get("dev")
                    or ""
                )

            info["interfaces"].append({
                "type": iface.get("type", ""),
                "source": source_value,
                "model": model.get("type", "") if model is not None else "",
                "mac": mac.get("address", "") if mac is not None else "",
            })

        for graphics in devices.findall("graphics"):
            info["graphics"].append({
                "type": graphics.get("type", ""),
                "port": graphics.get("port", ""),
                "listen": graphics.get("listen", ""),
            })

    return info


def list_all_vm_names() -> list[str]:
    result = run(["virsh", "list", "--all", "--name"], check=False)
    if result.returncode != 0:
        return []

    names = []
    for line in result.stdout.splitlines():
        name = line.strip()
        if name:
            names.append(name)

    return sorted(names, key=lambda value: value.lower())


def vm_state(vm_name: str) -> str:
    result = run(["virsh", "domstate", vm_name], check=False)
    if result.returncode != 0:
        return "unknown"

    state = result.stdout.strip()
    return state or "unknown"


def vm_state_map() -> dict[str, str]:
    states: dict[str, str] = {}

    for name in list_all_vm_names():
        states[name] = vm_state(name)

    return states



def vm_autostart(vm_name: str) -> bool:
    result = run(["virsh", "dominfo", vm_name], check=False)
    if result.returncode != 0:
        return False

    for line in result.stdout.splitlines():
        if line.lower().startswith("autostart:"):
            return "enable" in line.lower()

    return False


def vm_ips(vm_name: str) -> list[str]:
    result = run(["virsh", "domifaddr", vm_name], check=False)
    ips: list[str] = []

    if result.returncode != 0:
        return ips

    for line in split_lines(result.stdout):
        if line.startswith("Name") or line.startswith("-"):
            continue

        parts = line.split()
        if len(parts) >= 4 and "/" in parts[-1]:
            ips.append(parts[-1])

    return ips


def list_vm_inventory() -> dict[str, Any]:
    states = vm_state_map()
    vms = []

    for name, state in sorted(states.items(), key=lambda item: item[0].lower()):
        xml_text = virsh_xml(name)
        vm = parse_vm_xml(name, xml_text)
        vm["state"] = state
        vm["running"] = "running" in state.lower()
        vm["autostart"] = vm_autostart(name)

        if vm["running"]:
            vm["ips"] = vm_ips(name)
        else:
            vm["ips"] = []

        vms.append(vm)

    running = sum(1 for vm in vms if vm.get("running"))
    offline = len(vms) - running

    return {
        "total": len(vms),
        "running": running,
        "offline": offline,
        "vms": vms,
    }
