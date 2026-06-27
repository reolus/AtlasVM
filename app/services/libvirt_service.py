from __future__ import annotations

import html
import os
import re
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
    memory_mb: int = 2048
    vcpus: int = 2
    disk_gb: int = 20
    storage_pool: str = 'atlasvm-default'
    network: str = 'default'
    iso_path: str | None = None
    os_variant: str = 'generic'
    description: str | None = None
    start_after_create: bool = False
    autostart: bool = False
    firmware: str = 'bios'


class LibvirtService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.conn = libvirt.open(self.settings.libvirt_uri)
        if self.conn is None:
            raise RuntimeError(f'Unable to connect to libvirt URI {self.settings.libvirt_uri}')

    def close(self) -> None:
        if self.conn:
            self.conn.close()

    def list_storage_pools(self) -> list[dict[str, Any]]:
        pools = []
        for pool in self.conn.listAllStoragePools(0):
            info = pool.info()
            target_path = None
            try:
                target_path = ET.fromstring(pool.XMLDesc()).findtext('./target/path')
            except Exception:
                pass
            pools.append({
                'name': pool.name(),
                'uuid': pool.UUIDString(),
                'active': bool(pool.isActive()),
                'autostart': bool(pool.autostart()),
                'path': target_path,
                'capacity_gb': round(info[1] / 1024 / 1024 / 1024, 2),
                'allocation_gb': round(info[2] / 1024 / 1024 / 1024, 2),
                'available_gb': round(info[3] / 1024 / 1024 / 1024, 2),
            })
        return sorted(pools, key=lambda p: p['name'])

    def get_storage_pool(self, name: str) -> dict[str, Any]:
        pool = self.conn.storagePoolLookupByName(name)
        result = next((p for p in self.list_storage_pools() if p['name'] == name), {'name': name})
        result['volumes'] = self.list_pool_volumes(name)
        return result

    def list_pool_volumes(self, name: str) -> list[dict[str, Any]]:
        pool = self.conn.storagePoolLookupByName(name)
        if not pool.isActive():
            pool.create()
        pool.refresh(0)
        volumes = []
        for vol in pool.listAllVolumes(0):
            info = vol.info()
            volumes.append({
                'name': vol.name(),
                'path': vol.path(),
                'type': info[0],
                'capacity_gb': round(info[1] / 1024 / 1024 / 1024, 2),
                'allocation_gb': round(info[2] / 1024 / 1024 / 1024, 2),
            })
        return sorted(volumes, key=lambda v: v['name'])

    def list_isos(self) -> list[dict[str, Any]]:
        iso_dir = Path(self.settings.iso_path)
        if not iso_dir.exists():
            return []
        isos = []
        for path in sorted(iso_dir.glob('*')):
            if path.is_file() and path.suffix.lower() in {'.iso', '.img'}:
                st = path.stat()
                isos.append({'name': path.name, 'path': str(path), 'size_mb': round(st.st_size / 1024 / 1024, 2)})
        return isos

    def delete_iso(self, filename: str) -> None:
        path = (Path(self.settings.iso_path) / filename).resolve()
        root = Path(self.settings.iso_path).resolve()
        if root not in path.parents:
            raise ValueError('Invalid ISO path')
        path.unlink()
        self._refresh_pool_if_exists(self.settings.iso_pool)

    def refresh_storage_pool(self, name: str) -> None:
        pool = self.conn.storagePoolLookupByName(name)
        if not pool.isActive():
            pool.create()
        pool.refresh(0)

    def list_networks(self) -> list[dict[str, Any]]:
        networks = []
        for net in self.conn.listAllNetworks(0):
            bridge = None
            cidr = None
            try:
                bridge = net.bridgeName() if net.isActive() else None
                root = ET.fromstring(net.XMLDesc())
                ip = root.find('./ip')
                if ip is not None:
                    cidr = f"{ip.attrib.get('address')}/{ip.attrib.get('netmask')}"
            except Exception:
                pass
            networks.append({
                'name': net.name(),
                'uuid': net.UUIDString(),
                'active': bool(net.isActive()),
                'bridge': bridge,
                'autostart': bool(net.autostart()),
                'cidr': cidr,
            })
        return sorted(networks, key=lambda n: n['name'])

    def network_action(self, name: str, action: str) -> None:
        net = self.conn.networkLookupByName(name)
        if action == 'start' and not net.isActive():
            net.create()
        elif action == 'stop' and net.isActive():
            net.destroy()
        elif action == 'autostart-on':
            net.setAutostart(1)
        elif action == 'autostart-off':
            net.setAutostart(0)
        else:
            raise ValueError(f'Unsupported network action: {action}')

    def list_vms(self) -> list[dict[str, Any]]:
        domains = self.conn.listAllDomains(0)
        return sorted([self._domain_summary(domain) for domain in domains], key=lambda d: d['name'])

    def get_vm(self, name: str) -> dict[str, Any]:
        domain = self.conn.lookupByName(name)
        return self._domain_summary(domain, include_xml=True, include_snapshots=True)

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

    def set_autostart(self, name: str, enabled: bool) -> None:
        domain = self.conn.lookupByName(name)
        domain.setAutostart(1 if enabled else 0)

    def delete_vm(self, name: str, delete_disks: bool = False) -> None:
        domain = self.conn.lookupByName(name)
        if domain.isActive():
            domain.destroy()
        disk_paths = self._domain_disk_paths(domain) if delete_disks else []
        try:
            domain.undefineFlags(libvirt.VIR_DOMAIN_UNDEFINE_NVRAM)
        except Exception:
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
            raise RuntimeError('libvirt failed to define the VM')
        domain.setAutostart(1 if req.autostart else 0)
        if req.start_after_create:
            domain.create()
        return self._domain_summary(domain)

    def create_snapshot(self, vm_name: str, snapshot_name: str, description: str | None = None) -> dict[str, Any]:
        domain = self.conn.lookupByName(vm_name)
        self._validate_snapshot_name(snapshot_name)
        desc = html.escape(description or '')
        xml = f"""
        <domainsnapshot>
          <name>{html.escape(snapshot_name)}</name>
          <description>{desc}</description>
        </domainsnapshot>
        """
        snapshot = domain.snapshotCreateXML(xml, 0)
        return {'name': snapshot.getName()}

    def list_snapshots(self, vm_name: str) -> list[dict[str, Any]]:
        domain = self.conn.lookupByName(vm_name)
        snapshots = []
        for snap in domain.listAllSnapshots(0):
            root = ET.fromstring(snap.getXMLDesc())
            snapshots.append({
                'name': snap.getName(),
                'description': root.findtext('./description') or '',
                'created': root.findtext('./creationTime') or '',
            })
        return sorted(snapshots, key=lambda s: s['name'])

    def revert_snapshot(self, vm_name: str, snapshot_name: str) -> None:
        domain = self.conn.lookupByName(vm_name)
        snapshot = domain.snapshotLookupByName(snapshot_name, 0)
        domain.revertToSnapshot(snapshot, 0)

    def delete_snapshot(self, vm_name: str, snapshot_name: str) -> None:
        domain = self.conn.lookupByName(vm_name)
        snapshot = domain.snapshotLookupByName(snapshot_name, 0)
        snapshot.delete(0)

    def vnc_display(self, name: str) -> str | None:
        domain = self.conn.lookupByName(name)
        try:
            display = domain.XMLDesc()
            root = ET.fromstring(display)
            graphics = root.find("./devices/graphics[@type='vnc']")
            if graphics is None:
                return None
            port = graphics.attrib.get('port')
            if port and port != '-1':
                return str(int(port) - 5900) if not port.startswith(':') else port
        except Exception:
            pass
        try:
            result = subprocess.run(['virsh', 'vncdisplay', name], check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            value = result.stdout.strip()
            return value or None
        except Exception:
            return None

    def _validate_vm_request(self, req: VMCreateRequest) -> None:
        if not re.match(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$', req.name):
            raise ValueError('VM name must start with a letter or number and may contain letters, numbers, hyphens, and underscores')
        if req.memory_mb < 256:
            raise ValueError('memory_mb must be at least 256')
        if req.vcpus < 1:
            raise ValueError('vcpus must be at least 1')
        if req.disk_gb < 1:
            raise ValueError('disk_gb must be at least 1')
        if req.firmware not in {'bios', 'uefi'}:
            raise ValueError('firmware must be bios or uefi')
        if req.iso_path and not os.path.exists(req.iso_path):
            raise ValueError(f'ISO path does not exist: {req.iso_path}')
        self.conn.storagePoolLookupByName(req.storage_pool)
        self.conn.networkLookupByName(req.network)
        try:
            self.conn.lookupByName(req.name)
            raise ValueError(f'VM already exists: {req.name}')
        except libvirt.libvirtError:
            pass

    def _validate_snapshot_name(self, name: str) -> None:
        if not re.match(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$', name):
            raise ValueError('Snapshot name may contain letters, numbers, hyphens, and underscores')

    def _create_qcow2_disk(self, req: VMCreateRequest) -> str:
        pool = self.conn.storagePoolLookupByName(req.storage_pool)
        if not pool.isActive():
            pool.create()
        pool.refresh(0)
        pool_xml = ET.fromstring(pool.XMLDesc())
        target_path = pool_xml.findtext('./target/path')
        if not target_path:
            raise RuntimeError(f'Storage pool has no target path: {req.storage_pool}')
        disk_path = str(Path(target_path) / f'{req.name}.qcow2')
        if Path(disk_path).exists():
            raise ValueError(f'Disk already exists: {disk_path}')
        subprocess.run(['qemu-img', 'create', '-f', 'qcow2', disk_path, f'{req.disk_gb}G'], check=True)
        pool.refresh(0)
        return disk_path

    def _build_domain_xml(self, req: VMCreateRequest, disk_path: str) -> str:
        memory_kib = req.memory_mb * 1024
        cdrom_xml = ''
        boot_order = "<boot dev='cdrom'/><boot dev='hd'/>" if req.iso_path else "<boot dev='hd'/>"
        loader_xml = ''
        machine = 'q35' if req.firmware == 'uefi' else 'pc'
        if req.firmware == 'uefi':
            loader_xml = "<loader readonly='yes' type='pflash'>/usr/share/OVMF/OVMF_CODE.fd</loader>"
        if req.iso_path:
            cdrom_xml = f"""
            <disk type='file' device='cdrom'>
              <driver name='qemu' type='raw'/>
              <source file='{html.escape(req.iso_path)}'/>
              <target dev='sda' bus='sata'/>
              <readonly/>
            </disk>
            """
        metadata = html.escape(req.description or '')
        return f"""
        <domain type='kvm'>
          <name>{html.escape(req.name)}</name>
          <description>{metadata}</description>
          <memory unit='KiB'>{memory_kib}</memory>
          <currentMemory unit='KiB'>{memory_kib}</currentMemory>
          <vcpu placement='static'>{req.vcpus}</vcpu>
          <os>
            <type arch='x86_64' machine='{machine}'>hvm</type>
            {loader_xml}
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
              <source file='{html.escape(disk_path)}'/>
              <target dev='vda' bus='virtio'/>
            </disk>
            {cdrom_xml}
            <interface type='network'>
              <source network='{html.escape(req.network)}'/>
              <model type='virtio'/>
            </interface>
            <graphics type='vnc' port='-1' autoport='yes' listen='127.0.0.1'>
              <listen type='address' address='127.0.0.1'/>
            </graphics>
            <video><model type='virtio' heads='1' primary='yes'/></video>
            <input type='tablet' bus='usb'/>
            <console type='pty'/>
            <channel type='unix'>
              <target type='virtio' name='org.qemu.guest_agent.0'/>
            </channel>
          </devices>
        </domain>
        """

    def _domain_summary(self, domain: libvirt.virDomain, include_xml: bool = False, include_snapshots: bool = False) -> dict[str, Any]:
        state_code, _ = domain.state()
        info = domain.info()
        xml = domain.XMLDesc()
        root = ET.fromstring(xml)
        result = {
            'name': domain.name(),
            'uuid': domain.UUIDString(),
            'id': domain.ID() if domain.ID() != -1 else None,
            'state': self._state_name(state_code),
            'active': bool(domain.isActive()),
            'autostart': bool(domain.autostart()),
            'memory_mb': round(info[2] / 1024),
            'max_memory_mb': round(info[1] / 1024),
            'vcpus': info[3],
            'cpu_time_ns': info[4],
            'description': root.findtext('./description') or '',
            'disks': self._domain_disk_paths(domain),
            'interfaces': self._domain_interfaces(domain),
            'graphics': self._domain_graphics(domain),
        }
        if include_snapshots:
            try:
                result['snapshots'] = self.list_snapshots(domain.name())
            except Exception:
                result['snapshots'] = []
        if include_xml:
            result['xml'] = xml
        return result

    def _domain_disk_paths(self, domain: libvirt.virDomain) -> list[str]:
        paths = []
        root = ET.fromstring(domain.XMLDesc())
        for disk in root.findall('./devices/disk'):
            if disk.attrib.get('device') == 'disk':
                source = disk.find('source')
                if source is not None and 'file' in source.attrib:
                    paths.append(source.attrib['file'])
        return paths

    def _domain_interfaces(self, domain: libvirt.virDomain) -> list[dict[str, str | None]]:
        interfaces = []
        root = ET.fromstring(domain.XMLDesc())
        for iface in root.findall('./devices/interface'):
            mac = iface.find('mac')
            source = iface.find('source')
            model = iface.find('model')
            interfaces.append({
                'mac': mac.attrib.get('address') if mac is not None else None,
                'network': source.attrib.get('network') if source is not None else None,
                'bridge': source.attrib.get('bridge') if source is not None else None,
                'model': model.attrib.get('type') if model is not None else None,
            })
        return interfaces

    def _domain_graphics(self, domain: libvirt.virDomain) -> list[dict[str, str | None]]:
        graphics_list = []
        root = ET.fromstring(domain.XMLDesc())
        for graphics in root.findall('./devices/graphics'):
            port = graphics.attrib.get('port')
            display = None
            if port and port not in {'-1', '0'}:
                try:
                    display = f":{int(port) - 5900}"
                except ValueError:
                    display = None
            graphics_list.append({
                'type': graphics.attrib.get('type'),
                'listen': graphics.attrib.get('listen'),
                'port': port,
                'display': display,
            })
        return graphics_list

    def _refresh_pool_if_exists(self, name: str) -> None:
        try:
            pool = self.conn.storagePoolLookupByName(name)
            if pool.isActive():
                pool.refresh(0)
        except Exception:
            pass

    def _state_name(self, state_code: int) -> str:
        return {
            libvirt.VIR_DOMAIN_NOSTATE: 'nostate',
            libvirt.VIR_DOMAIN_RUNNING: 'running',
            libvirt.VIR_DOMAIN_BLOCKED: 'blocked',
            libvirt.VIR_DOMAIN_PAUSED: 'paused',
            libvirt.VIR_DOMAIN_SHUTDOWN: 'shutdown',
            libvirt.VIR_DOMAIN_SHUTOFF: 'shutoff',
            libvirt.VIR_DOMAIN_CRASHED: 'crashed',
            libvirt.VIR_DOMAIN_PMSUSPENDED: 'suspended',
        }.get(state_code, 'unknown')
