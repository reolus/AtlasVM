# API Summary

Base path: `/api/v1`

Authentication: HTTP Basic using `.env` values.

## Endpoints

- `GET /health`
- `GET /host`
- `GET /storage-pools`
- `GET /networks`
- `GET /vms`
- `POST /vms`
- `GET /vms/{name}`
- `POST /vms/{name}/start`
- `POST /vms/{name}/shutdown`
- `POST /vms/{name}/force-stop`
- `POST /vms/{name}/reboot`
- `DELETE /vms/{name}`
- `GET /events`

## Create VM payload

```json
{
  "name": "debian-test-01",
  "memory_mb": 2048,
  "vcpus": 2,
  "disk_gb": 20,
  "storage_pool": "default",
  "network": "default",
  "iso_path": "/var/lib/libvirt/images/debian.iso",
  "os_variant": "generic"
}
```

## Delete VM payload

```json
{
  "delete_disks": false
}
```
