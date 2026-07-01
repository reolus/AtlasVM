# AtlasVM Product Editions

AtlasVM now splits into two planned product lines: a free single-node edition and a future premium enterprise edition.

## AtlasVM Community Edition

AtlasVM Community Edition is the single-node product.

Included:

- Local Debian/libvirt/KVM host management
- Local VM inventory and VM detail
- Local VM power actions
- Local console access
- Local storage pool management
- Local network management
- ISO library and ISO attach/eject
- VM snapshots
- VM backups
- Task history and task kill
- Audit log
- Doctor checks
- ZFS pages
- Host network page
- Local users and settings where enabled

Not included:

- Multi-node registration
- Remote node inventory
- Remote VM detail
- Remote VM actions
- Clusters
- Organization or virtual datacenter hierarchy
- Folder or VM server group hierarchy
- Enterprise manager appliance
- Scoped cluster/folder/VM RBAC
- License activation requirement

AtlasVM Community Edition should remain useful without an AtlasVM account, license server, or enterprise repository. Humanity may still invent three ways to complicate that, but the product should not help them.

## AtlasVM Enterprise

AtlasVM Enterprise is the future paid product line.

Planned features:

- Multi-node management
- Cluster management
- Organization / Virtual Datacenter hierarchy
- Folder / VM Server Group hierarchy
- Scoped RBAC by cluster, folder, server group, VM, and action
- Standalone manager application or appliance similar in role to vCenter
- Licensing activation
- AtlasVM-hosted patch, update, and feature repository
- Feature-gated premium modules
- Enterprise backup, migration, replication, and lifecycle features later

Enterprise work should happen in a separate branch or module set so standalone does not become a free product wearing a premium product's skeleton as a hat.
