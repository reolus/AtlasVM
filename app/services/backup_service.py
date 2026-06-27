from __future__ import annotations

import json
import shutil
import subprocess
import tarfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.services.libvirt_service import LibvirtService


@dataclass
class BackupResult:
    vm_name: str
    backup_dir: str
    archive_path: str | None
    metadata_path: str
    disk_count: int
    xml_path: str


class BackupService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.backup_root = Path(self.settings.backup_path)
        self.backup_root.mkdir(parents=True, exist_ok=True)

    def create_backup(self, vm_name: str, compress: bool = True, require_shutdown: bool | None = None) -> BackupResult:
        require_shutdown = self.settings.backup_require_shutdown if require_shutdown is None else require_shutdown
        lv = LibvirtService()
        try:
            vm = lv.get_vm(vm_name)
            if require_shutdown and vm.get('state') == 'running':
                raise RuntimeError('Shutdown-only backup is enabled. Stop the VM before backing it up.')
            safe_name = self._safe(vm_name)
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
            dest = self.backup_root / safe_name / stamp
            dest.mkdir(parents=True, exist_ok=False)

            xml_path = dest / f'{safe_name}.xml'
            xml_path.write_text(vm.get('xml') or '', encoding='utf-8')

            disk_dir = dest / 'disks'
            disk_dir.mkdir()
            copied_disks = []
            for disk in vm.get('disks', []):
                src = Path(disk)
                if not src.exists():
                    copied_disks.append({'source': str(src), 'copied': False, 'error': 'source missing'})
                    continue
                target = disk_dir / src.name
                self._copy_disk(src, target)
                copied_disks.append({'source': str(src), 'target': str(target), 'copied': True})

            metadata = {
                'vm_name': vm_name,
                'created_at': stamp,
                'state': vm.get('state'),
                'memory_mb': vm.get('memory_mb'),
                'vcpus': vm.get('vcpus'),
                'uuid': vm.get('uuid'),
                'disks': copied_disks,
                'xml': str(xml_path),
                'format': 'atlasvm-backup-v1',
            }
            metadata_path = dest / 'metadata.json'
            metadata_path.write_text(json.dumps(metadata, indent=2), encoding='utf-8')

            archive_path = None
            if compress:
                archive_path = str(self._archive(dest))
            self._prune(vm_name)
            return BackupResult(vm_name, str(dest), archive_path, str(metadata_path), len(copied_disks), str(xml_path))
        finally:
            lv.close()

    def list_backups(self, vm_name: str | None = None) -> list[dict[str, Any]]:
        roots = [self.backup_root / self._safe(vm_name)] if vm_name else [p for p in self.backup_root.iterdir() if p.is_dir()] if self.backup_root.exists() else []
        backups: list[dict[str, Any]] = []
        for vm_root in roots:
            if not vm_root.exists():
                continue
            for item in vm_root.iterdir():
                if item.is_dir() and (item / 'metadata.json').exists():
                    data = json.loads((item / 'metadata.json').read_text(encoding='utf-8'))
                    archive = item.with_suffix('.tar.zst')
                    if not archive.exists():
                        archive = item.with_suffix('.tar.gz')
                    backups.append({
                        'vm_name': data.get('vm_name') or vm_root.name,
                        'created_at': data.get('created_at') or item.name,
                        'path': str(item),
                        'archive': str(archive) if archive.exists() else None,
                        'disk_count': len(data.get('disks', [])),
                        'state': data.get('state'),
                    })
        return sorted(backups, key=lambda b: b['created_at'], reverse=True)


    def delete_backup(self, backup_dir: str) -> None:
        backup_path = Path(backup_dir).resolve()
        root_path = self.backup_root.resolve()
        if root_path not in backup_path.parents:
            raise ValueError('Backup path is outside the AtlasVM backup root')
        if not (backup_path / 'metadata.json').exists():
            raise ValueError('Backup directory does not contain metadata.json')
        archive_zst = backup_path.with_suffix('.tar.zst')
        archive_gz = backup_path.with_suffix('.tar.gz')
        shutil.rmtree(backup_path, ignore_errors=False)
        archive_zst.unlink(missing_ok=True)
        archive_gz.unlink(missing_ok=True)


    def restore_as_new_vm(self, backup_dir: str, new_name: str, storage_pool: str | None = None) -> dict[str, str]:
        """Restore a backup as a new VM with copied disks, new name, no UUID, and regenerated MAC addresses."""
        import re
        import uuid
        import xml.etree.ElementTree as ET

        if not re.match(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$', new_name):
            raise ValueError('New VM name must start with a letter or number and may contain letters, numbers, hyphens, and underscores')

        backup_path = Path(backup_dir).resolve()
        root_path = self.backup_root.resolve()
        if root_path not in backup_path.parents:
            raise ValueError('Backup path is outside the AtlasVM backup root')

        metadata_path = backup_path / 'metadata.json'
        xml_path = next(backup_path.glob('*.xml'), None)
        if not metadata_path.exists() or xml_path is None:
            raise ValueError('Backup directory does not contain metadata.json and VM XML')

        metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
        lv = LibvirtService()
        try:
            try:
                lv.conn.lookupByName(new_name)
                raise ValueError(f'VM already exists: {new_name}')
            except Exception as exc:
                if 'VM already exists' in str(exc):
                    raise

            storage_pool = storage_pool or self.settings.default_storage_pool
            pool = lv.conn.storagePoolLookupByName(storage_pool)
            if not pool.isActive():
                pool.create()
            pool.refresh(0)
            pool_xml = ET.fromstring(pool.XMLDesc())
            target_dir = pool_xml.findtext('./target/path')
            if not target_dir:
                raise RuntimeError(f'Storage pool has no target path: {storage_pool}')

            domain_xml = xml_path.read_text(encoding='utf-8')
            domain_root = ET.fromstring(domain_xml)
            name_elem = domain_root.find('./name')
            if name_elem is None:
                name_elem = ET.SubElement(domain_root, 'name')
            name_elem.text = new_name

            uuid_elem = domain_root.find('./uuid')
            if uuid_elem is not None:
                domain_root.remove(uuid_elem)

            # Remove VNC fixed ports so libvirt can assign fresh console ports.
            for graphics in domain_root.findall('./devices/graphics'):
                if graphics.attrib.get('type') == 'vnc':
                    graphics.attrib['port'] = '-1'
                    graphics.attrib['autoport'] = 'yes'

            # Remove MACs so libvirt generates fresh ones.
            for iface in domain_root.findall('./devices/interface'):
                mac = iface.find('mac')
                if mac is not None:
                    iface.remove(mac)

            copied = []
            disk_records = [d for d in metadata.get('disks', []) if d.get('copied') and d.get('target')]
            disk_index = 1
            for disk_elem in domain_root.findall('./devices/disk'):
                if disk_elem.attrib.get('device') != 'disk':
                    continue
                source = disk_elem.find('source')
                if source is None:
                    continue
                record = disk_records[disk_index - 1] if disk_index - 1 < len(disk_records) else None
                if record is None:
                    continue
                backup_disk = Path(record['target'])
                if not backup_disk.exists():
                    raise FileNotFoundError(f'Backup disk missing: {backup_disk}')
                dest = Path(target_dir) / f'{new_name}-disk{disk_index}.qcow2'
                if dest.exists():
                    raise FileExistsError(f'Destination disk already exists: {dest}')
                self._copy_disk(backup_disk, dest)
                source.attrib.clear()
                source.attrib['file'] = str(dest)
                driver = disk_elem.find('driver')
                if driver is not None:
                    driver.attrib['type'] = 'qcow2'
                copied.append(str(dest))
                disk_index += 1

            # Clear template marker during restore.
            desc = domain_root.find('./description')
            if desc is not None and desc.text:
                desc.text = desc.text.replace('[ATLASVM_TEMPLATE]', '').strip()

            new_xml = ET.tostring(domain_root, encoding='unicode')
            domain = lv.conn.defineXML(new_xml)
            if domain is None:
                raise RuntimeError('libvirt failed to define restored VM')
            pool.refresh(0)
            return {'status': 'ok', 'name': domain.name(), 'disks': ', '.join(copied)}
        finally:
            lv.close()

    def restore_definition(self, backup_dir: str, new_name: str | None = None) -> dict[str, str]:
        backup_path = Path(backup_dir).resolve()
        metadata_path = backup_path / 'metadata.json'
        xml_path = next(backup_path.glob('*.xml'), None)
        if not metadata_path.exists() or xml_path is None:
            raise ValueError('Backup directory does not contain metadata.json and VM XML')
        xml = xml_path.read_text(encoding='utf-8')
        if new_name:
            xml = self._replace_name(xml, new_name)
        lv = LibvirtService()
        try:
            domain = lv.conn.defineXML(xml)
            if domain is None:
                raise RuntimeError('libvirt failed to define restored VM')
            return {'status': 'ok', 'name': domain.name()}
        finally:
            lv.close()

    def _copy_disk(self, src: Path, target: Path) -> None:
        if shutil.which('qemu-img') and src.suffix.lower() in {'.qcow2', '.raw', '.img'}:
            result = subprocess.run(['qemu-img', 'convert', '-O', 'qcow2', str(src), str(target)], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
            if result.returncode == 0:
                return
        shutil.copy2(src, target)

    def _archive(self, directory: Path) -> Path:
        if shutil.which('zstd'):
            tar_path = directory.with_suffix('.tar')
            zst_path = directory.with_suffix('.tar.zst')
            with tarfile.open(tar_path, 'w') as tf:
                tf.add(directory, arcname=directory.name)
            result = subprocess.run(['zstd', '-q', '-f', str(tar_path), '-o', str(zst_path)], check=False)
            tar_path.unlink(missing_ok=True)
            if result.returncode == 0:
                return zst_path
        gz_path = directory.with_suffix('.tar.gz')
        with tarfile.open(gz_path, 'w:gz') as tf:
            tf.add(directory, arcname=directory.name)
        return gz_path


    def prune_backups(self, vm_name: str | None = None, keep_last: int | None = None) -> dict[str, object]:
        """Apply the simple AtlasVM retention policy and return deleted paths.

        Phase 6 keeps this intentionally conservative: keep the most recent N
        backups per VM and delete older backup directories plus matching archives.
        """
        keep = max(0, int(self.settings.backup_keep_last if keep_last is None else keep_last))
        targets: list[str]
        if vm_name:
            targets = [vm_name]
        else:
            targets = [p.name for p in self.backup_root.iterdir() if p.is_dir()] if self.backup_root.exists() else []
        deleted: list[str] = []
        for target in targets:
            backups = self.list_backups(target)
            for old in backups[keep:]:
                path = Path(old['path'])
                if path.exists():
                    shutil.rmtree(path, ignore_errors=True)
                    deleted.append(str(path))
                if old.get('archive'):
                    archive = Path(old['archive'])
                    archive.unlink(missing_ok=True)
                    deleted.append(str(archive))
        return {'status': 'ok', 'keep_last': keep, 'deleted': deleted, 'deleted_count': len(deleted)}

    def retention_policy(self) -> dict[str, object]:
        return {
            'backup_keep_last': int(self.settings.backup_keep_last),
            'backup_path': str(self.backup_root),
            'policy': 'keep_last_per_vm',
        }

    def _prune(self, vm_name: str) -> None:
        keep = max(0, int(self.settings.backup_keep_last))
        if keep <= 0:
            return
        backups = self.list_backups(vm_name)
        for old in backups[keep:]:
            path = Path(old['path'])
            shutil.rmtree(path, ignore_errors=True)
            if old.get('archive'):
                Path(old['archive']).unlink(missing_ok=True)

    def _safe(self, name: str) -> str:
        return ''.join(c if c.isalnum() or c in '-_' else '_' for c in name)

    def _replace_name(self, xml: str, new_name: str) -> str:
        import re
        if not re.match(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$', new_name):
            raise ValueError('VM name must start with a letter or number and may contain letters, numbers, hyphens, and underscores')
        xml = re.sub(r'<name>.*?</name>', f'<name>{new_name}</name>', xml, count=1)
        xml = re.sub(r'<uuid>.*?</uuid>', '', xml, count=1)
        return xml
