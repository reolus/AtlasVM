from __future__ import annotations

import os
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import libvirt

from app.core.config import get_settings


@dataclass
class VMCreateRequest:
    name: str
    memory_mb: int
    vcpus: int
    disk_gb: int
    storage_pool: str
    network: str
    iso_path: str | None = None
    os_variant: str = "generic"


class LibvirtService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.conn = libvirt.open(self.settings.libvirt_uri)
        if self.conn is None:
            raise RuntimeError(f"Unable to connect to libvirt at {self.settings.libvirt_uri}")

    def close(self) -> None:
        if self.conn:
            self.conn.close()

    def node_info(self) -> dict[str, Any]:
        info = self.conn.getInfo()
        return {
            "model": info[0],
            "memory_mb": info[1],
            "cpus": info[2],
            "mhz": info[3],
            "nodes": info[4],
            "sockets": info[5],
            "cores": info[6],
            "threads": info[7],
        }

    def list_storage_pools(self) -> list[dict[str, Any]]:
        pools = []
        for pool in self.conn.listAllStoragePools(0):
            info = pool.info()
            pools.append({
                "name": pool.name(),
                "active": bool(pool.isActive()),
                "capacity_gb": round(info[1] / 1024 / 1024 / 1024, 2),
                "allocation_gb": round(info[2] / 1024 / 1024 / 1024, 2),
                "available_gb": round(info[3] / 1024 / 1024 / 1024, 2),
            })
        return pools

    def list_networks(self) -> list[dict[str, Any]]:
        networks = []
        for net in self.conn.listAllNetworks(0):
            networks.append({
                "name": net.name(),
                "active": bool(net.isActive()),
                "bridge": net.bridgeName() if net.isActive() else None,
                "autostart": bool(net.autostart()),
            })
        return networks

    def list_vms(self) -> list[dict[str, Any]]:
        domains = self.conn.listAllDomains(0)
        return [self._domain_summary(domain) for domain in domains]

    def get_vm(self, name: str) -> dict[str, Any]:
        domain = self.conn.lookupByName(name)
        return self._domain_summary(domain, include_xml=True)

    def start_vm(self, name: str) -> None:
        domain = self.conn.lookupByName(name)
        if not domain.isActive():
            domain.create()

    def shutdown_vm(self, name: str) -> None:
        domain = self.conn.lookupByName(name)
        if domain.isActive():
            domain.shutdown()

    def force_stop_vm(self, name: str) -> None:
        domain = self.conn.lookupByName(name)
        if domain.isActive():
            domain.destroy()

    def reboot_vm(self, name: str) -> None:
        domain = self.conn.lookupByName(name)
        if domain.isActive():
            domain.reboot()

    def delete_vm(self, name: str, delete_disks: bool = False) -> None:
        domain = self.conn.lookupByName(name)
        if domain.isActive():
            domain.destroy()
        disk_paths = self._domain_disk_paths(domain) if delete_disks else []
        domain.undefine()
        for disk_path in disk_paths:
            try:
                Path(disk_path).unlink(missing_ok=True)
            except Exception:
                pass

    def create_vm(self, req: VMCreateRequest) -> dict[str, Any]:
        self._validate_vm_request(req)
        disk_path = self._create_qcow2_disk(req)
        xml = self._build_domain_xml(req, disk_path)
        domain = self.conn.defineXML(xml)
        if domain is None:
            raise RuntimeError("libvirt failed to define the VM")
        return self._domain_summary(domain)

    def _validate_vm_request(self, req: VMCreateRequest) -> None:
        if not req.name.replace("-", "").replace("_", "").isalnum():
            raise ValueError("VM name may contain only letters, numbers, hyphens, and underscores")
        if req.memory_mb < 256:
            raise ValueError("memory_mb must be at least 256")
        if req.vcpus < 1:
            raise ValueError("vcpus must be at least 1")
        if req.disk_gb < 1:
            raise ValueError("disk_gb must be at least 1")
        if req.iso_path and not os.path.exists(req.iso_path):
            raise ValueError(f"ISO path does not exist: {req.iso_path}")
        self.conn.storagePoolLookupByName(req.storage_pool)
        self.conn.networkLookupByName(req.network)
        try:
            self.conn.lookupByName(req.name)
            raise ValueError(f"VM already exists: {req.name}")
        except libvirt.libvirtError:
            pass

    def _create_qcow2_disk(self, req: VMCreateRequest) -> str:
        pool = self.conn.storagePoolLookupByName(req.storage_pool)
        if not pool.isActive():
            pool.create()
        pool.refresh(0)
        pool_xml = ET.fromstring(pool.XMLDesc())
        target_path = pool_xml.findtext("./target/path")
        if not target_path:
            raise RuntimeError(f"Storage pool has no target path: {req.storage_pool}")
        disk_path = str(Path(target_path) / f"{req.name}.qcow2")
        subprocess.run([
            "qemu-img", "create", "-f", "qcow2", disk_path, f"{req.disk_gb}G"
        ], check=True)
        pool.refresh(0)
        return disk_path

    def _build_domain_xml(self, req: VMCreateRequest, disk_path: str) -> str:
        memory_kib = req.memory_mb * 1024
        cdrom_xml = ""
        boot_order = "<boot dev='cdrom'/><boot dev='hd'/>" if req.iso_path else "<boot dev='hd'/>"
        if req.iso_path:
            cdrom_xml = f"""
            <disk type='file' device='cdrom'>
              <driver name='qemu' type='raw'/>
              <source file='{req.iso_path}'/>
              <target dev='sda' bus='sata'/>
              <readonly/>
            </disk>
            """

        return f"""
        <domain type='kvm'>
          <name>{req.name}</name>
          <memory unit='KiB'>{memory_kib}</memory>
          <currentMemory unit='KiB'>{memory_kib}</currentMemory>
          <vcpu placement='static'>{req.vcpus}</vcpu>
          <os>
            <type arch='x86_64' machine='pc'>hvm</type>
            {boot_order}
          </os>
          <features>
            <acpi/>
            <apic/>
          </features>
          <cpu mode='host-model'/>
          <clock offset='utc'/>
          <on_poweroff>destroy</on_poweroff>
          <on_reboot>restart</on_reboot>
          <on_crash>restart</on_crash>
          <devices>
            <emulator>/usr/bin/qemu-system-x86_64</emulator>
            <disk type='file' device='disk'>
              <driver name='qemu' type='qcow2'/>
              <source file='{disk_path}'/>
              <target dev='vda' bus='virtio'/>
            </disk>
            {cdrom_xml}
            <interface type='network'>
              <source network='{req.network}'/>
              <model type='virtio'/>
            </interface>
            <graphics type='vnc' port='-1' autoport='yes' listen='127.0.0.1'/>
            <video><model type='virtio'/></video>
            <console type='pty'/>
            <channel type='unix'>
              <target type='virtio' name='org.qemu.guest_agent.0'/>
            </channel>
          </devices>
        </domain>
        """

    def _domain_summary(self, domain: libvirt.virDomain, include_xml: bool = False) -> dict[str, Any]:
        state_code, _ = domain.state()
        state = self._state_name(state_code)
        info = domain.info()
        result = {
            "name": domain.name(),
            "uuid": domain.UUIDString(),
            "id": domain.ID() if domain.ID() != -1 else None,
            "state": state,
            "active": bool(domain.isActive()),
            "memory_mb": round(info[2] / 1024),
            "max_memory_mb": round(info[1] / 1024),
            "vcpus": info[3],
            "cpu_time_ns": info[4],
            "disks": self._domain_disk_paths(domain),
        }
        if include_xml:
            result["xml"] = domain.XMLDesc()
        return result

    def _domain_disk_paths(self, domain: libvirt.virDomain) -> list[str]:
        paths = []
        root = ET.fromstring(domain.XMLDesc())
        for disk in root.findall("./devices/disk"):
            if disk.attrib.get("device") == "disk":
                source = disk.find("source")
                if source is not None and "file" in source.attrib:
                    paths.append(source.attrib["file"])
        return paths

    def _state_name(self, state_code: int) -> str:
        return {
            libvirt.VIR_DOMAIN_NOSTATE: "nostate",
            libvirt.VIR_DOMAIN_RUNNING: "running",
            libvirt.VIR_DOMAIN_BLOCKED: "blocked",
            libvirt.VIR_DOMAIN_PAUSED: "paused",
            libvirt.VIR_DOMAIN_SHUTDOWN: "shutdown",
            libvirt.VIR_DOMAIN_SHUTOFF: "shutoff",
            libvirt.VIR_DOMAIN_CRASHED: "crashed",
            libvirt.VIR_DOMAIN_PMSUSPENDED: "suspended",
        }.get(state_code, "unknown")
