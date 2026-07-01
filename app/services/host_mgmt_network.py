from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any


PLAN_FILE = Path("/opt/atlasvm/atlasvm_host_network.json")
STATE_FILE = Path("/opt/atlasvm/atlasvm_host_network_state.json")
BACKUP_ROOT = Path("/opt/atlasvm/backups/network")
NETWORKD_DIR = Path("/etc/systemd/network")
ROLLBACK_SCRIPT = Path("/usr/local/sbin/atlasvm-network-rollback")
ROLLBACK_SERVICE = Path("/etc/systemd/system/atlasvm-network-rollback.service")
ROLLBACK_TIMER = Path("/etc/systemd/system/atlasvm-network-rollback.timer")


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
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


def service_active(name: str) -> bool:
    result = run(["systemctl", "is-active", name], check=False)
    return result.returncode == 0 and result.stdout.strip() == "active"


def detect_stack() -> dict[str, Any]:
    return {
        "systemd_networkd_active": service_active("systemd-networkd"),
        "networkmanager_active": service_active("NetworkManager"),
        "networking_active": service_active("networking"),
        "networkd_dir_exists": NETWORKD_DIR.exists(),
    }


def list_links() -> list[dict[str, Any]]:
    result = run(["ip", "-j", "addr", "show"], check=False)
    if result.returncode != 0:
        return []

    try:
        raw = json.loads(result.stdout)
    except Exception:
        return []

    links = []
    for item in raw:
        name = item.get("ifname", "")
        addr_info = item.get("addr_info", [])
        addresses = []
        for addr in addr_info:
            local = addr.get("local")
            prefix = addr.get("prefixlen")
            family = addr.get("family")
            if local and prefix is not None:
                addresses.append(f"{local}/{prefix} ({family})")

        links.append({
            "name": name,
            "state": item.get("operstate", ""),
            "mac": item.get("address", ""),
            "addresses": addresses,
        })

    return links


def routes() -> list[dict[str, Any]]:
    result = run(["ip", "-j", "route", "show"], check=False)
    if result.returncode != 0:
        return []
    try:
        return json.loads(result.stdout)
    except Exception:
        return []


def validate_vlan(vlan_tag: str | None) -> str:
    vlan_tag = str(vlan_tag or "").strip()
    if not vlan_tag:
        return ""

    vlan = int(vlan_tag)
    if vlan < 1 or vlan > 4094:
        raise RuntimeError("VLAN tag must be between 1 and 4094.")

    return str(vlan)


def normalize_cidr(static_ip: str, subnet: str) -> str:
    static_ip = static_ip.strip()
    subnet = subnet.strip()

    if not static_ip:
        raise RuntimeError("Static IP is required.")

    if "/" in static_ip:
        return static_ip

    if not subnet:
        raise RuntimeError("Subnet/CIDR is required.")

    if subnet.isdigit():
        return f"{static_ip}/{subnet}"

    # Convert dotted netmask to prefix.
    import ipaddress
    network = ipaddress.IPv4Network(f"0.0.0.0/{subnet}")
    return f"{static_ip}/{network.prefixlen}"


def save_plan(
    management_interface: str,
    vlan_tag: str,
    static_ip: str,
    subnet: str,
    gateway: str,
    dns_servers: str,
) -> dict[str, Any]:
    vlan_tag = validate_vlan(vlan_tag)

    plan = {
        "management_interface": management_interface.strip(),
        "vlan_tag": vlan_tag,
        "static_ip": static_ip.strip(),
        "subnet": subnet.strip(),
        "gateway": gateway.strip(),
        "dns_servers": dns_servers.strip(),
        "saved_at": int(time.time()),
    }

    if not plan["management_interface"]:
        raise RuntimeError("Management interface is required.")

    if not plan["static_ip"]:
        raise RuntimeError("Static IP is required.")

    if not plan["gateway"]:
        raise RuntimeError("Gateway is required.")

    normalize_cidr(plan["static_ip"], plan["subnet"])

    write_json(PLAN_FILE, plan)
    return plan


def load_plan() -> dict[str, Any]:
    return read_json(PLAN_FILE)


def backup_network_config() -> Path:
    ts = time.strftime("%Y%m%d-%H%M%S")
    backup_dir = BACKUP_ROOT / ts
    backup_dir.mkdir(parents=True, exist_ok=True)

    if NETWORKD_DIR.exists():
        shutil.copytree(NETWORKD_DIR, backup_dir / "systemd-network", dirs_exist_ok=True)

    interfaces = Path("/etc/network/interfaces")
    if interfaces.exists():
        shutil.copy2(interfaces, backup_dir / "interfaces")

    interfaces_d = Path("/etc/network/interfaces.d")
    if interfaces_d.exists():
        shutil.copytree(interfaces_d, backup_dir / "interfaces.d", dirs_exist_ok=True)

    write_json(backup_dir / "stack.json", detect_stack())
    return backup_dir


def write_networkd_config(plan: dict[str, Any]) -> list[Path]:
    NETWORKD_DIR.mkdir(parents=True, exist_ok=True)

    iface = plan["management_interface"]
    vlan_tag = validate_vlan(plan.get("vlan_tag"))
    address = normalize_cidr(plan["static_ip"], plan["subnet"])
    gateway = plan["gateway"].strip()
    dns = plan.get("dns_servers", "").replace(",", " ").strip()

    written: list[Path] = []

    if vlan_tag:
        vlan_name = f"mgmt{vlan_tag}"
        if len(vlan_name) > 15:
            vlan_name = f"m{vlan_tag}"

        netdev = NETWORKD_DIR / f"10-atlasvm-{vlan_name}.netdev"
        netdev.write_text(f"""[NetDev]
Name={vlan_name}
Kind=vlan

[VLAN]
Id={vlan_tag}
""")
        written.append(netdev)

        parent_network = NETWORKD_DIR / f"10-atlasvm-{iface}.network"
        parent_network.write_text(f"""[Match]
Name={iface}

[Network]
VLAN={vlan_name}
LinkLocalAddressing=no
IPv6AcceptRA=no
""")
        written.append(parent_network)

        vlan_network = NETWORKD_DIR / f"11-atlasvm-{vlan_name}.network"
        vlan_network.write_text(f"""[Match]
Name={vlan_name}

[Network]
Address={address}
Gateway={gateway}
DNS={dns}
LinkLocalAddressing=no
IPv6AcceptRA=no
""")
        written.append(vlan_network)

    else:
        network = NETWORKD_DIR / f"10-atlasvm-{iface}.network"
        network.write_text(f"""[Match]
Name={iface}

[Network]
Address={address}
Gateway={gateway}
DNS={dns}
LinkLocalAddressing=no
IPv6AcceptRA=no
""")
        written.append(network)

    for path in written:
        os.chmod(path, 0o644)

    return written


def install_rollback(backup_dir: Path, timeout_seconds: int) -> None:
    ROLLBACK_SCRIPT.write_text(f"""#!/bin/sh
set -eu

echo "AtlasVM management network rollback starting"

if [ -d "{backup_dir}/systemd-network" ]; then
  mkdir -p /etc/systemd/network
  rm -f /etc/systemd/network/10-atlasvm-*.network /etc/systemd/network/10-atlasvm-*.netdev /etc/systemd/network/11-atlasvm-*.network
  cp -a "{backup_dir}/systemd-network/." /etc/systemd/network/
fi

if [ -f "{backup_dir}/interfaces" ]; then
  cp -a "{backup_dir}/interfaces" /etc/network/interfaces
fi

if [ -d "{backup_dir}/interfaces.d" ]; then
  mkdir -p /etc/network/interfaces.d
  cp -a "{backup_dir}/interfaces.d/." /etc/network/interfaces.d/
fi

systemctl restart systemd-networkd 2>/dev/null || true
systemctl restart networking 2>/dev/null || true

echo "AtlasVM management network rollback complete"
""")
    os.chmod(ROLLBACK_SCRIPT, 0o755)

    ROLLBACK_SERVICE.write_text(f"""[Unit]
Description=AtlasVM Management Network Rollback

[Service]
Type=oneshot
ExecStart={ROLLBACK_SCRIPT}
""")

    ROLLBACK_TIMER.write_text(f"""[Unit]
Description=AtlasVM Management Network Rollback Timer

[Timer]
OnActiveSec={timeout_seconds}
Unit=atlasvm-network-rollback.service

[Install]
WantedBy=timers.target
""")

    run(["systemctl", "daemon-reload"])
    run(["systemctl", "enable", "--now", "atlasvm-network-rollback.timer"])


def cancel_rollback() -> None:
    run(["systemctl", "disable", "--now", "atlasvm-network-rollback.timer"], check=False)
    run(["systemctl", "reset-failed", "atlasvm-network-rollback.service"], check=False)

    state = read_json(STATE_FILE)
    state["confirmed_at"] = int(time.time())
    state["rollback_cancelled"] = True
    write_json(STATE_FILE, state)


def apply_plan(timeout_seconds: int = 120) -> dict[str, Any]:
    stack = detect_stack()

    if stack["networkmanager_active"]:
        raise RuntimeError("NetworkManager is active. Phase 8.2 currently supports systemd-networkd first. Disable/convert NetworkManager or add a NetworkManager backend.")

    plan = load_plan()
    if not plan:
        raise RuntimeError("No management network plan has been saved.")

    backup_dir = backup_network_config()
    written = write_networkd_config(plan)

    state = {
        "applied_at": int(time.time()),
        "backup_dir": str(backup_dir),
        "plan": plan,
        "written_files": [str(p) for p in written],
        "rollback_timeout_seconds": timeout_seconds,
        "rollback_cancelled": False,
    }
    write_json(STATE_FILE, state)

    install_rollback(backup_dir, timeout_seconds)

    run(["systemctl", "enable", "--now", "systemd-networkd"])
    run(["systemctl", "restart", "systemd-networkd"])

    return state


def rollback_now() -> None:
    if not ROLLBACK_SCRIPT.exists():
        raise RuntimeError("Rollback script does not exist.")
    run([str(ROLLBACK_SCRIPT)])
