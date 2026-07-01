# AtlasVM Phase 7 Design

Phase 7 expands AtlasVM network management beyond basic start/stop controls.

## Goals

- Create libvirt networks from the web interface.
- Support NAT, isolated, routed, and bridge-backed network modes.
- Edit inactive networks safely.
- Delete unused networks with safety checks.
- Show network detail pages with DHCP leases, attached VMs, XML, bridge, CIDR, and autostart state.
- Preserve existing Phase 1 through Phase 6 behavior.

## Network Modes

### NAT

Creates a libvirt network with `<forward mode="nat">`, optional bridge name, gateway CIDR, and optional DHCP range.

### Isolated

Creates a libvirt network with no forward element. VMs on the network can communicate with each other but are not forwarded through the host by libvirt.

### Routed

Creates a libvirt network with `<forward mode="route">`. This requires host and upstream routing to be configured correctly.

### Bridge-backed

Creates a libvirt network backed by an existing Linux bridge, using `<forward mode="bridge">`. The bridge must already exist on the host.

## Safety Controls

AtlasVM refuses to redefine active networks. Stop a network before editing it.

AtlasVM refuses to delete:

- The configured default network.
- Any network currently attached to one or more VMs.

## UI Additions

- `/networks` network inventory
- `/networks/new` create network form
- `/networks/{name}` detail page
- `/networks/{name}/edit` edit form
- POST actions for start, stop, autostart, delete

## Notes

Bridge-backed networking can disrupt host access if the Linux bridge is misconfigured. AtlasVM does not create host bridges in Phase 7. Create and validate host bridges at the OS level first.
