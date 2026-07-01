# Licensing and Update Repository Roadmap

## Product principle

AtlasVM Community Edition should not require license activation. It should remain a local, single-node virtualization management product.

AtlasVM Enterprise features should require license activation. Licensing should gate features and modules. It must not corrupt, lock, or rewrite local VM data. A failed license check should disable premium workflows gracefully, not turn the host into a dramatic little paperweight.

## License-gated Enterprise features

Initial license gates should cover:

- Multi-node management
- Cluster management
- Manager mode
- Organization / Virtual Datacenter hierarchy
- Folder / VM Server Group hierarchy
- Scoped RBAC by cluster/folder/group/VM/action
- Enterprise backup/migration features later
- Premium feature modules

## Licensing behavior

Recommended behavior:

- Store license state locally with signed validation data.
- Allow grace periods for temporary repository or license-server outages.
- Keep Community Edition features available even if Enterprise licensing expires.
- Never block access to local VM inventory, console, backup history, or host recovery tools because of license state.
- Log license changes and validation failures to audit.
- Make premium-only UI elements hidden or disabled with clear messaging.

## AtlasVM update repository roadmap

AtlasVM should eventually provide a hosted repository for patches, updates, and feature downloads.

Repository capabilities:

- Signed update packages
- Signed patch metadata
- Feature modules
- Version channels:
  - `stable`
  - `testing`
  - `enterprise`
- Local offline update bundle support
- License-aware feature downloads
- Rollback support
- Compatibility metadata by AtlasVM version, Debian version, libvirt version, and module version
- Security advisory metadata

## Update safety rules

Updates should support:

- Preflight checks before install
- Backup of changed files
- Rollback manifest
- Service restart validation
- Route/UI audit after patching
- Clear failure state in the task log

## Future implementation phases

1. Define edition metadata and feature flags.
2. Add non-enforcing local feature flag registry.
3. Add signed license file validation.
4. Add Enterprise feature gates around multi-node/cluster/manager modules.
5. Build AtlasVM repository metadata format.
6. Add signed update package installation.
7. Add offline update bundle support.
8. Add rollback and version-channel management.
