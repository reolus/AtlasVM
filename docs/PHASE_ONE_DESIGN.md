# Phase One Design

## Goal

Build a single-node virtualization manager that sits above libvirt and provides a web UI plus REST API for basic VM lifecycle management.

## Non-goals

This phase does not attempt to implement clustering, live migration, HA, Ceph, SDN, distributed task queues, multi-tenant RBAC, or backup orchestration. Those are real features, not decorative stickers.

## Components

### Web UI

The UI is intentionally simple and server-rendered using Jinja2. That keeps the first version easy to install, audit, and modify.

### API

The REST API lives under `/api/v1`. It is protected by HTTP Basic authentication using credentials from `.env`.

### VM control

VM control uses libvirt through `libvirt-python`. VM definitions are generated as libvirt domain XML.

### Storage

The first version creates qcow2 disk images in an existing libvirt storage pool. The default is usually `/var/lib/libvirt/images` through the `default` pool.

### Networking

The first version attaches VMs to an existing libvirt network, usually `default`. Real bridge creation is intentionally left for Phase Two because network automation is where people heroically lock themselves out of their own servers.

### Event logging

Event history is stored in SQLite using SQLAlchemy. This is enough for Phase One and easy to replace with PostgreSQL later.

## Security model

Phase One uses one local admin account. It should be deployed behind VPN or a reverse proxy with TLS. Direct internet exposure is a bad idea with a cape.

## Future changes

- Replace HTTP Basic with session auth or OIDC
- Add noVNC console access
- Add Cloud-init templates
- Add ZFS/LVM-thin support
- Add role-based access control
- Add scheduled snapshot and backup jobs
- Add node agent split for future clustering
