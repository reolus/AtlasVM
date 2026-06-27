# AtlasVM API

All endpoints except `/api/v1/health` require HTTP Basic authentication.

Base path:

```text
/api/v1
```

## Health

```http
GET /api/v1/health
```

Returns product and phase status.

## Host

```http
GET /api/v1/host
```

Returns host CPU, memory, and root disk summary.

## Virtual Machines

```http
GET /api/v1/vms
GET /api/v1/vms/{name}
POST /api/v1/vms
POST /api/v1/vms/{name}/start
POST /api/v1/vms/{name}/shutdown
POST /api/v1/vms/{name}/reboot
POST /api/v1/vms/{name}/force-stop
POST /api/v1/vms/{name}/autostart-on
POST /api/v1/vms/{name}/autostart-off
DELETE /api/v1/vms/{name}
```

Create body:

```json
{
  "name": "test-alpine-01",
  "memory_mb": 1024,
  "vcpus": 1,
  "disk_gb": 8,
  "storage_pool": "atlasvm-default",
  "network": "default",
  "iso_path": "/atlasvm-vmdata/iso/alpine-standard.iso",
  "description": "Alpine installer test",
  "firmware": "bios",
  "start_after_create": true,
  "autostart": false
}
```

Delete body:

```json
{
  "delete_disks": true
}
```

## Console

```http
POST /api/v1/vms/{name}/console
```

Starts a noVNC proxy for the VM and returns the proxy URL, VNC display, VNC port, and proxy port.

## Snapshots

```http
GET /api/v1/vms/{name}/snapshots
POST /api/v1/vms/{name}/snapshots
POST /api/v1/vms/{name}/snapshots/{snapshot}/revert
DELETE /api/v1/vms/{name}/snapshots/{snapshot}
```

Create body:

```json
{
  "name": "before-update",
  "description": "Before package updates"
}
```

## ISO Library

```http
GET /api/v1/isos
```

The UI provides upload/delete actions at `/isos`.

## Storage

```http
GET /api/v1/storage-pools
GET /api/v1/storage-pools/{name}
POST /api/v1/storage-pools/{name}/refresh
```

## Networks

```http
GET /api/v1/networks
POST /api/v1/networks/{name}/start
POST /api/v1/networks/{name}/stop
POST /api/v1/networks/{name}/autostart-on
POST /api/v1/networks/{name}/autostart-off
```

## Events and Tasks

```http
GET /api/v1/events
GET /api/v1/tasks
```
