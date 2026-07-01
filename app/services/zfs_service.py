from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import get_settings


_SIZE_RE = re.compile(r"^([0-9.]+)([KMGTPZE]?)(?:i?B?)?$", re.I)
_SIZE_MULTIPLIER = {
    '': 1,
    'K': 1024,
    'M': 1024 ** 2,
    'G': 1024 ** 3,
    'T': 1024 ** 4,
    'P': 1024 ** 5,
    'E': 1024 ** 6,
    'Z': 1024 ** 7,
}


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)


def _available(binary: str) -> bool:
    return shutil.which(binary) is not None


def zfs_available() -> bool:
    return _available('zfs') and _available('zpool')


def _parse_size(value: str) -> int | None:
    value = str(value or '').strip()
    if value in {'-', 'none', ''}:
        return None
    match = _SIZE_RE.match(value)
    if not match:
        return None
    number = float(match.group(1))
    suffix = match.group(2).upper()
    return int(number * _SIZE_MULTIPLIER.get(suffix, 1))


def _safe_token(value: str) -> str:
    return ''.join(c if c.isalnum() or c in '-_.' else '_' for c in value)


def _validate_dataset(dataset: str) -> str:
    dataset = dataset.strip()
    if not dataset or dataset.startswith('-') or '@' in dataset or '..' in dataset:
        raise ValueError('Invalid ZFS dataset name')
    if not re.match(r'^[A-Za-z0-9_.:/-]+$', dataset):
        raise ValueError('Invalid characters in ZFS dataset name')
    return dataset


def _validate_snapshot_name(name: str) -> str:
    name = name.strip()
    if not name or name.startswith('-') or any(c in name for c in ' /\\:@'):
        raise ValueError('Snapshot name may not contain spaces, slash, backslash, colon, or at-sign')
    if not re.match(r'^[A-Za-z0-9_.-]+$', name):
        raise ValueError('Invalid characters in ZFS snapshot name')
    return name


def _validate_snapshot(snapshot: str) -> str:
    snapshot = snapshot.strip()
    if not snapshot or snapshot.startswith('-') or '@' not in snapshot or '..' in snapshot:
        raise ValueError('Invalid ZFS snapshot name')
    dataset, snap = snapshot.rsplit('@', 1)
    _validate_dataset(dataset)
    _validate_snapshot_name(snap)
    return snapshot


def pool_status() -> dict[str, Any]:
    if not zfs_available():
        return {'available': False, 'error': 'zfs/zpool commands not found', 'pools': [], 'warnings': []}
    pools = []
    list_result = _run(['zpool', 'list', '-H', '-o', 'name,size,alloc,free,capacity,health'])
    if list_result.returncode != 0:
        return {'available': True, 'error': list_result.stderr.strip(), 'pools': [], 'warnings': []}
    for line in list_result.stdout.splitlines():
        parts = line.split('\t')
        if len(parts) >= 6:
            capacity_text = parts[4].replace('%', '')
            try:
                capacity = int(capacity_text)
            except ValueError:
                capacity = None
            pools.append({
                'name': parts[0],
                'size': parts[1],
                'allocated': parts[2],
                'free': parts[3],
                'capacity': parts[4],
                'capacity_percent': capacity,
                'health': parts[5],
            })
    return {'available': True, 'pools': pools, 'warnings': health_warnings(pools), 'checked_at': datetime.now(timezone.utc).isoformat()}


def health_warnings(pools: list[dict[str, Any]] | None = None) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    if not zfs_available():
        return [{'severity': 'warning', 'target': 'zfs', 'message': 'zfs/zpool commands not found'}]
    if pools is None:
        pools = pool_status().get('pools', [])
    for pool in pools:
        name = pool.get('name', 'unknown')
        health = pool.get('health', 'UNKNOWN')
        if health != 'ONLINE':
            warnings.append({'severity': 'critical', 'target': name, 'message': f'ZFS pool {name} health is {health}'})
        capacity = pool.get('capacity_percent')
        if isinstance(capacity, int):
            if capacity >= 90:
                warnings.append({'severity': 'critical', 'target': name, 'message': f'ZFS pool {name} is {capacity}% full'})
            elif capacity >= 80:
                warnings.append({'severity': 'warning', 'target': name, 'message': f'ZFS pool {name} is {capacity}% full'})
    scrub_result = _run(['zpool', 'status'])
    if scrub_result.returncode == 0:
        text = scrub_result.stdout.lower()
        if 'scrub repaired' in text and 'with 0 errors' not in text:
            warnings.append({'severity': 'warning', 'target': 'scrub', 'message': 'Recent ZFS scrub reported repairs or errors; review zpool status'})
        if 'no known data errors' not in text and 'errors: no known data errors' not in text:
            warnings.append({'severity': 'warning', 'target': 'zfs', 'message': 'zpool status does not report clean data state'})
    return warnings


def datasets() -> list[dict[str, Any]]:
    if not zfs_available():
        return []
    result = _run(['zfs', 'list', '-H', '-o', 'name,used,avail,refer,mountpoint,usedsnap,quota,refquota'])
    rows: list[dict[str, Any]] = []
    if result.returncode != 0:
        return rows
    for line in result.stdout.splitlines():
        parts = line.split('\t')
        if len(parts) >= 8:
            used_bytes = _parse_size(parts[1])
            avail_bytes = _parse_size(parts[2])
            total_bytes = (used_bytes or 0) + (avail_bytes or 0)
            capacity_percent = int(((used_bytes or 0) / total_bytes) * 100) if total_bytes > 0 else None
            rows.append({
                'name': parts[0],
                'used': parts[1],
                'available': parts[2],
                'referenced': parts[3],
                'mountpoint': parts[4],
                'usedsnap': parts[5],
                'quota': parts[6],
                'refquota': parts[7],
                'capacity_percent': capacity_percent,
            })
    return rows


def snapshots(limit: int = 250) -> list[dict[str, str]]:
    if not zfs_available():
        return []
    result = _run(['zfs', 'list', '-H', '-t', 'snapshot', '-o', 'name,used,refer,creation', '-s', 'creation'])
    rows: list[dict[str, str]] = []
    if result.returncode != 0:
        return rows
    for line in result.stdout.splitlines()[-limit:]:
        parts = line.split('\t')
        if len(parts) >= 4:
            dataset, snap = parts[0].rsplit('@', 1) if '@' in parts[0] else (parts[0], '')
            rows.append({'name': parts[0], 'dataset': dataset, 'snapshot': snap, 'used': parts[1], 'referenced': parts[2], 'creation': parts[3]})
    rows.reverse()
    return rows


def scrub(pool: str) -> dict[str, str]:
    if not zfs_available():
        raise RuntimeError('zfs/zpool commands not found')
    pool = _validate_dataset(pool)
    result = _run(['zpool', 'scrub', pool])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return {'status': 'ok', 'pool': pool}


def create_snapshot(dataset: str, name: str, recursive: bool = False) -> dict[str, str]:
    if not zfs_available():
        raise RuntimeError('zfs command not found')
    dataset = _validate_dataset(dataset)
    name = _validate_snapshot_name(name)
    full = f'{dataset}@{name}'
    cmd = ['zfs', 'snapshot']
    if recursive:
        cmd.append('-r')
    cmd.append(full)
    result = _run(cmd)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return {'status': 'ok', 'snapshot': full}


def destroy_snapshot(snapshot: str, recursive: bool = False) -> dict[str, str]:
    if not zfs_available():
        raise RuntimeError('zfs command not found')
    snapshot = _validate_snapshot(snapshot)
    cmd = ['zfs', 'destroy']
    if recursive:
        cmd.append('-r')
    cmd.append(snapshot)
    result = _run(cmd)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return {'status': 'ok', 'snapshot': snapshot}


def send_snapshot(snapshot: str, destination_dir: str | None = None, recursive: bool = False, compress: bool = True) -> dict[str, str]:
    if not zfs_available():
        raise RuntimeError('zfs command not found')
    snapshot = _validate_snapshot(snapshot)
    settings = get_settings()
    base_dir = Path(destination_dir or Path(settings.backup_path) / 'zfs-exports').resolve()
    base_dir.mkdir(parents=True, exist_ok=True)
    filename = _safe_token(snapshot.replace('/', '_').replace('@', '__')) + '.zfs'
    if compress and shutil.which('zstd'):
        filename += '.zst'
    output_path = base_dir / filename
    if output_path.exists():
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        output_path = base_dir / (output_path.name + f'.{stamp}')
    send_cmd = ['zfs', 'send']
    if recursive:
        send_cmd.append('-R')
    send_cmd.append(snapshot)
    if compress and shutil.which('zstd'):
        send = subprocess.Popen(send_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        zstd = subprocess.Popen(['zstd', '-q', '-f', '-o', str(output_path)], stdin=send.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if send.stdout is not None:
            send.stdout.close()
        _, zstd_err = zstd.communicate()
        send_err = send.stderr.read() if send.stderr is not None else b''
        send_rc = send.wait()
        if send_rc != 0 or zstd.returncode != 0:
            output_path.unlink(missing_ok=True)
            raise RuntimeError((send_err.decode() or zstd_err.decode() or 'zfs send failed').strip())
    else:
        with output_path.open('wb') as fh:
            result = subprocess.run(send_cmd, stdout=fh, stderr=subprocess.PIPE, check=False)
        if result.returncode != 0:
            output_path.unlink(missing_ok=True)
            raise RuntimeError(result.stderr.decode().strip() if isinstance(result.stderr, bytes) else str(result.stderr))
    metadata_path = output_path.with_suffix(output_path.suffix + '.json')
    metadata_path.write_text(json.dumps({
        'snapshot': snapshot,
        'path': str(output_path),
        'created_at': datetime.now(timezone.utc).isoformat(),
        'recursive': recursive,
        'compressed': output_path.suffix.endswith('zst'),
        'format': 'atlasvm-zfs-send-v1',
    }, indent=2), encoding='utf-8')
    return {'status': 'ok', 'snapshot': snapshot, 'path': str(output_path), 'metadata': str(metadata_path)}


def exports() -> list[dict[str, Any]]:
    settings = get_settings()
    root = Path(settings.backup_path) / 'zfs-exports'
    if not root.exists():
        return []
    rows: list[dict[str, Any]] = []
    for item in sorted(root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if item.is_file() and (item.name.endswith('.zfs') or item.name.endswith('.zfs.zst')):
            meta = item.with_suffix(item.suffix + '.json')
            data: dict[str, Any] = {}
            if meta.exists():
                try:
                    data = json.loads(meta.read_text(encoding='utf-8'))
                except Exception:
                    data = {}
            rows.append({
                'path': str(item),
                'name': item.name,
                'size': item.stat().st_size,
                'created_at': data.get('created_at') or datetime.fromtimestamp(item.stat().st_mtime).isoformat(),
                'snapshot': data.get('snapshot', ''),
                'recursive': data.get('recursive', False),
                'compressed': item.name.endswith('.zst'),
            })
    return rows


def delete_export(path: str) -> None:
    settings = get_settings()
    root = (Path(settings.backup_path) / 'zfs-exports').resolve()
    target = Path(path).resolve()
    if root not in target.parents:
        raise ValueError('Export path is outside the AtlasVM ZFS export root')
    if not target.exists() or not target.is_file():
        raise ValueError('ZFS export file does not exist')
    meta = target.with_suffix(target.suffix + '.json')
    target.unlink()
    meta.unlink(missing_ok=True)


def dataset_for_path(path: str) -> str | None:
    if not zfs_available():
        return None
    path_obj = Path(path).resolve()
    candidates = []
    for ds in datasets():
        mountpoint = ds.get('mountpoint')
        if not mountpoint or mountpoint in {'-', 'none'}:
            continue
        try:
            mp = Path(mountpoint).resolve()
        except Exception:
            continue
        try:
            path_obj.relative_to(mp)
            candidates.append((len(str(mp)), ds['name']))
        except ValueError:
            continue
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]
