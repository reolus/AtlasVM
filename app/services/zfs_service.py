from __future__ import annotations

import shutil
import subprocess
from datetime import datetime
from typing import Any


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)


def _available(binary: str) -> bool:
    return shutil.which(binary) is not None


def zfs_available() -> bool:
    return _available('zfs') and _available('zpool')


def pool_status() -> dict[str, Any]:
    if not zfs_available():
        return {'available': False, 'error': 'zfs/zpool commands not found'}
    pools = []
    list_result = _run(['zpool', 'list', '-H', '-o', 'name,size,alloc,free,health'])
    if list_result.returncode != 0:
        return {'available': True, 'error': list_result.stderr.strip(), 'pools': []}
    for line in list_result.stdout.splitlines():
        parts = line.split('\t')
        if len(parts) >= 5:
            pools.append({'name': parts[0], 'size': parts[1], 'allocated': parts[2], 'free': parts[3], 'health': parts[4]})
    return {'available': True, 'pools': pools, 'checked_at': datetime.utcnow().isoformat() + 'Z'}


def datasets() -> list[dict[str, str]]:
    if not zfs_available():
        return []
    result = _run(['zfs', 'list', '-H', '-o', 'name,used,avail,refer,mountpoint'])
    rows = []
    if result.returncode != 0:
        return rows
    for line in result.stdout.splitlines():
        parts = line.split('\t')
        if len(parts) >= 5:
            rows.append({'name': parts[0], 'used': parts[1], 'available': parts[2], 'referenced': parts[3], 'mountpoint': parts[4]})
    return rows


def snapshots(limit: int = 250) -> list[dict[str, str]]:
    if not zfs_available():
        return []
    result = _run(['zfs', 'list', '-H', '-t', 'snapshot', '-o', 'name,used,refer,creation', '-s', 'creation'])
    rows = []
    if result.returncode != 0:
        return rows
    for line in result.stdout.splitlines()[-limit:]:
        parts = line.split('\t')
        if len(parts) >= 4:
            rows.append({'name': parts[0], 'used': parts[1], 'referenced': parts[2], 'creation': parts[3]})
    rows.reverse()
    return rows


def scrub(pool: str) -> dict[str, str]:
    if not zfs_available():
        raise RuntimeError('zfs/zpool commands not found')
    result = _run(['zpool', 'scrub', pool])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return {'status': 'ok', 'pool': pool}


def create_snapshot(dataset: str, name: str) -> dict[str, str]:
    if not zfs_available():
        raise RuntimeError('zfs command not found')
    if not name or any(c in name for c in ' /\\:@'):
        raise ValueError('Snapshot name may not contain spaces, slash, backslash, colon, or at-sign')
    full = f'{dataset}@{name}'
    result = _run(['zfs', 'snapshot', full])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return {'status': 'ok', 'snapshot': full}
