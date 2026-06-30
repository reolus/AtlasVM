from __future__ import annotations

import hashlib
import json
import os
import platform
import socket
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from app.core.version import atlasvm_version as _atlasvm_version

NODE_FILE = Path('/opt/atlasvm/atlasvm_nodes.json')
NODE_ID_FILE = Path('/opt/atlasvm/atlasvm_node_id')
NODE_TOKEN_FILE = Path('/opt/atlasvm/atlasvm_node_token')


def now_ts() -> int:
    return int(time.time())


def read_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text() or json.dumps(default))
    except Exception:
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    tmp.replace(path)


def run(cmd: list[str], timeout: int = 5) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, text=True, capture_output=True, check=False, timeout=timeout)
    except Exception as exc:
        return subprocess.CompletedProcess(cmd, 1, '', str(exc))


def get_local_node_id() -> str:
    if NODE_ID_FILE.exists():
        value = NODE_ID_FILE.read_text().strip()
        if value:
            return value

    node_id = f'node-{uuid.uuid4().hex[:12]}'
    NODE_ID_FILE.write_text(node_id + '\n')
    return node_id


def get_node_token() -> str:
    env_token = os.environ.get('ATLASVM_NODE_TOKEN', '').strip()
    if env_token:
        return env_token

    if NODE_TOKEN_FILE.exists():
        value = NODE_TOKEN_FILE.read_text().strip()
        if value:
            return value

    token = uuid.uuid4().hex + uuid.uuid4().hex
    NODE_TOKEN_FILE.write_text(token + '\n')
    NODE_TOKEN_FILE.chmod(0o600)
    return token


def token_fingerprint(token: str) -> str:
    token = token or ''
    if not token:
        return ''
    return hashlib.sha256(token.encode('utf-8')).hexdigest()[:16]


def validate_node_token(provided: str | None) -> bool:
    expected = get_node_token()
    provided = (provided or '').strip()
    if not expected:
        return True
    return bool(provided) and hashlib.sha256(provided.encode()).hexdigest() == hashlib.sha256(expected.encode()).hexdigest()


def normalize_api_url(value: str) -> str:
    value = (value or '').strip().rstrip('/')
    if not value:
        raise RuntimeError('API URL is required.')
    if not value.startswith('http://') and not value.startswith('https://'):
        value = 'https://' + value
    return value.rstrip('/')


def list_nodes() -> list[dict[str, Any]]:
    data = read_json(NODE_FILE, [])
    if isinstance(data, dict):
        data = list(data.values())
    if not isinstance(data, list):
        return []
    return sorted(data, key=lambda item: str(item.get('name') or item.get('api_url') or '').lower())


def save_nodes(nodes: list[dict[str, Any]]) -> None:
    write_json(NODE_FILE, nodes)


def get_node(node_id: str) -> dict[str, Any] | None:
    node_id = (node_id or '').strip()
    for node in list_nodes():
        if node.get('node_id') == node_id:
            return node
    return None


def upsert_node(
    name: str,
    api_url: str,
    token: str = '',
    role: str = 'worker',
    enabled: bool = True,
    node_id: str = '',
) -> dict[str, Any]:
    name = (name or '').strip()
    api_url = normalize_api_url(api_url)
    token = (token or '').strip()
    role = (role or 'worker').strip()
    node_id = (node_id or '').strip() or f'node-{uuid.uuid4().hex[:12]}'

    if not name:
        name = api_url.replace('https://', '').replace('http://', '').split('/')[0]

    nodes = list_nodes()
    existing = None
    for node in nodes:
        if node.get('node_id') == node_id or node.get('api_url') == api_url:
            existing = node
            break

    if existing is None:
        existing = {'node_id': node_id, 'created_at': now_ts()}
        nodes.append(existing)

    existing.update({
        'node_id': existing.get('node_id') or node_id,
        'name': name,
        'api_url': api_url,
        'role': role,
        'enabled': bool(enabled),
        'updated_at': now_ts(),
    })

    if token:
        existing['token'] = token
        existing['token_fingerprint'] = token_fingerprint(token)
    else:
        existing.setdefault('token', '')
        existing['token_fingerprint'] = token_fingerprint(existing.get('token', ''))

    save_nodes(nodes)
    return existing


def delete_node(node_id: str) -> bool:
    nodes = list_nodes()
    kept = [node for node in nodes if node.get('node_id') != node_id]
    save_nodes(kept)
    return len(kept) != len(nodes)


def local_primary_ip() -> str:
    result = run(['ip', '-4', 'route', 'get', '1.1.1.1'])
    if result.returncode == 0:
        parts = result.stdout.split()
        if 'src' in parts:
            idx = parts.index('src') + 1
            if idx < len(parts):
                return parts[idx]
    return '127.0.0.1'


def atlasvm_version() -> str:
    return _atlasvm_version()


def local_node_self() -> dict[str, Any]:
    hostname = socket.gethostname()
    ip = local_primary_ip()
    token = get_node_token()
    return {
        'node_id': get_local_node_id(),
        'name': hostname,
        'hostname': hostname,
        'management_ip': ip,
        'api_url': f'https://{ip}',
        'role': 'local',
        'version': atlasvm_version(),
        'token_fingerprint': token_fingerprint(token),
        'platform': platform.platform(),
        'python': platform.python_version(),
        'time': now_ts(),
    }


def ensure_local_node_registered() -> dict[str, Any]:
    local = local_node_self()
    nodes = list_nodes()
    for node in nodes:
        if node.get('node_id') == local['node_id']:
            node.update({
                'name': node.get('name') or local['name'],
                'api_url': node.get('api_url') or local['api_url'],
                'role': node.get('role') or 'local',
                'enabled': True,
                'local': True,
                'updated_at': now_ts(),
                'token_fingerprint': local['token_fingerprint'],
            })
            save_nodes(nodes)
            return node

    node = {
        'node_id': local['node_id'],
        'name': local['name'],
        'api_url': local['api_url'],
        'role': 'local',
        'enabled': True,
        'local': True,
        'created_at': now_ts(),
        'updated_at': now_ts(),
        'token': get_node_token(),
        'token_fingerprint': local['token_fingerprint'],
    }
    nodes.append(node)
    save_nodes(nodes)
    return node
