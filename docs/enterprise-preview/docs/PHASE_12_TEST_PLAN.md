# AtlasVM Phase 12 Multi-Node Test Plan

This test plan validates the manager UI and node communication before expanding remote mutation behavior.

## Test 1: Single-node baseline

1. Open `/`.
2. Open `/vms`.
3. Open one local VM detail page.
4. Open `/nodes`.
5. Open `/nodes/compatibility`.

Expected result:

- Pages load without server errors.
- No unexpected horizontal overflow.
- Local VM actions remain available.

## Test 2: Node registration

1. Register or verify the second node.
2. Open `/nodes`.
3. Confirm the remote node appears.
4. Confirm node status is online or reports a clear error.

Expected result:

- Node appears with API URL, status, and health information.
- Long IDs and URLs wrap inside cards.

## Test 3: Compatibility report

1. Open `/nodes/compatibility`.
2. Review every node panel.
3. Document warnings and errors.

Expected result:

- Blocking errors are visible.
- Warnings are clear and wrapped.
- Phase 12 gate status is obvious.

## Test 4: Remote inventory

1. Open `/vms`.
2. Filter by the remote node.
3. Confirm remote VMs appear.
4. Confirm local/remote badges are correct.

Expected result:

- Remote VMs are visible.
- Remote VMs do not show unsupported destructive actions.

## Test 5: Remote VM detail

1. Open a remote VM detail page.
2. Confirm owner node badge.
3. Confirm state, vCPU, memory, disk, and NIC information.
4. Confirm storage and network values wrap cleanly.

Expected result:

- Remote detail page renders using the modern UI.
- Remote mutation boundary is clear.

## Test 6: Remote console link

1. Open the remote VM detail page.
2. Use the remote console action.

Expected result:

- Console opens through the owning node path or provides a clear failure.
- No manager-side traceback occurs.

## Test 7: Remote safe power action

Only run this test on a non-production VM.

1. Select a remote test VM.
2. Record its starting state.
3. Run one safe power action appropriate to state.
4. Refresh remote inventory.
5. Confirm state changed or a clear error was reported.

Expected result:

- Action either succeeds cleanly or fails with a readable message.
- No unsupported remote mutation appears.

## Test 8: Offline node behavior

If safe to simulate:

1. Stop or disconnect the remote node API.
2. Refresh `/nodes`.
3. Open compatibility report.
4. Attempt to view remote VM detail.

Expected result:

- Offline status is clear.
- UI does not crash.
- Remote actions are unavailable or fail safely.

## Test 9: Recovery validation

1. Restore remote node API if it was stopped.
2. Restart AtlasVM manager.
3. Re-open `/nodes`, `/vms`, and `/nodes/compatibility`.

Expected result:

- Cluster view returns to expected state.
- No stale fatal condition remains.

