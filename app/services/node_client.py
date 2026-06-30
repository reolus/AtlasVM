from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.request
from typing import Any


def fetch_node_json(node: dict[str, Any], path: str, timeout: int = 5) -> dict[str, Any]:
    api_url = str(node.get('api_url') or '').rstrip('/')
    if not api_url:
        return {'ok': False, 'error': 'Node API URL is missing.'}

    token = str(node.get('token') or '')
    url = api_url + path
    request = urllib.request.Request(url, headers={'Accept': 'application/json'})
    if token:
        request.add_header('X-AtlasVM-Node-Token', token)

    ctx = ssl._create_unverified_context()
    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=ctx) as response:
            raw = response.read().decode('utf-8')
            data = json.loads(raw or '{}')
            data.setdefault('ok', True)
            data['_latency_ms'] = int((time.time() - started) * 1000)
            return data
    except urllib.error.HTTPError as exc:
        return {'ok': False, 'error': f'HTTP {exc.code}: {exc.reason}', '_latency_ms': int((time.time() - started) * 1000)}
    except Exception as exc:
        return {'ok': False, 'error': str(exc), '_latency_ms': int((time.time() - started) * 1000)}


def node_health(node: dict[str, Any]) -> dict[str, Any]:
    return fetch_node_json(node, '/api/node/health')


def node_inventory_remote(node: dict[str, Any]) -> dict[str, Any]:
    return fetch_node_json(node, '/api/node/inventory', timeout=8)


def enrich_nodes(nodes: list[dict[str, Any]], include_inventory: bool = False) -> list[dict[str, Any]]:
    enriched = []
    for node in nodes:
        item = dict(node)
        if not item.get('enabled', True):
            item['remote_status'] = {'ok': False, 'error': 'disabled'}
        else:
            item['remote_status'] = node_inventory_remote(item) if include_inventory else node_health(item)
        enriched.append(item)
    return enriched
