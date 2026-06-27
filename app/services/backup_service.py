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
                raise RuntimeError('Shutdown-only backup is enabled. Stop the VM before backing it up, or uncheck shutdown-only backup.')
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
