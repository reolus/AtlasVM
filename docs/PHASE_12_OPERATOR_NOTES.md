# AtlasVM Phase 12 Operator Notes

These notes define how to operate during the first multi-node test pass.

## Testing posture

Treat Phase 12 as a controlled validation of node visibility and safe remote action boundaries. Do not use production VMs as first-pass remote action targets.

## Remote actions intentionally limited

The manager UI should expose only conservative remote actions during the first test pass:

- View remote VM inventory.
- View remote VM detail.
- Open remote console path.
- Run safe remote power operations where supported.

The UI should not offer remote disk, NIC, ISO, snapshot, backup, clone, or delete operations yet.

## What to capture

For each issue, capture:

- Page URL.
- Node ID/name.
- VM name.
- Action attempted.
- Browser screenshot.
- `journalctl -u atlasvm -n 120 --no-pager` output.
- Whether the issue is cosmetic, read-only data, action failure, or safety boundary failure.

## Cosmetic issues

Examples:

- Wrapping problems.
- Bad badge text.
- Poor spacing.
- Missing icon or label.

Continue testing if the UI remains safe and functional.

## Functional issues

Examples:

- Remote VM inventory missing.
- Wrong state shown.
- Node status stale.
- Console link broken.

Pause the affected test and continue only with unrelated read-only checks.

## Safety issues

Examples:

- Wrong VM targeted.
- Wrong node targeted.
- Unsupported remote mutation exposed.
- Disk/NIC/snapshot/delete action appears on a remote VM.

Stop testing immediately and use the rollback plan.

## Promotion criteria after first pass

Only consider expanding remote mutation behavior after:

- Remote inventory is stable.
- Compatibility checks are understandable.
- Remote detail pages are reliable.
- Safe power actions work or fail cleanly.
- Offline node behavior is safe.
- Rollback path is proven.

