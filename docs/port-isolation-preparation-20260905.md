# Downstream port isolation preparation

The requested next experiment is port 10, then port 5, then port 6, each disabled
for approximately 60 seconds. Each port must be restored, regain carrier, and
complete 90 seconds of native LACP observation before proceeding. A separate
120-second test removes all three branches together only after the individual
tests complete without a recovery signal. No firmware change, switch reboot,
Firewalla bond cycle, or VLAN/LAG write is part of this experiment.

The two earlier port-10 attempts lasted approximately five seconds before an
incorrect comparison of live LAG state flags triggered early restoration. They
were not completed one-minute tests. Ports 5 and 6 were never disabled. The
private checkpoint is `backups/port-isolation-20260905/PAUSED.md`.

## Implementation and readiness

The reviewed code is now in [the local coordinator](../tools/port_isolation.py)
and [the portable router worker](../tools/port_isolation_agent.py), with
[regression tests](../tests/test_port_isolation.py). Historical launchers and
readiness files are not reused.

Preparation downloads a fresh full snapshot and opaque backup, checks the
production pair and the Mac's management ingress, starts a bounded two-hour
passive recorder, and stages a new worker on Firewalla. It rehearses the actual
independent restoration service: authenticate, verify identity and configuration,
publish readiness, then exit without arming a disable deadline. A second full
snapshot must match the first. Preparation makes no switch configuration writes.

The prepared authorization expires after one hour and is bound to the worker
hash, configuration, and Firewalla boot. Expiration never starts a test. Starting
a trial requires a separate explicit launch; arming does not schedule an outage.

Before each disable, its restoration service must already have authenticated and
verified the switch. Restoration survives loss of the Mac and of the parent
controller. Authentication, identity checks, writes, and readback failures remain
inside a retry loop; systemd restarts a failed restoration worker. The worker
retains credentials until cleanup. A lost parent or an explicit early-restore
marker triggers restoration before the normal deadline. Only restoration writes
are retried; an uncertain disable stops the sequence.

All port writes use the already-observed single-port QSS handler and preserve
the saved speed and flow-control settings. Only ports 10, 5, and 6 are permitted.
For the combined test, they are disabled sequentially and restored in the order
6, 5, 10. The deadline is measured from immediately before the first disable
request, not from a precisely measured physical link transition. Recorded request,
acknowledgment, disabled-state, and restored-state timestamps bound each outage.

Actual configuration fields are compared separately from operational up/down,
negotiated speed, and flow-control status. Port settings, LAG configuration, and
PVIDs are checked on Firewalla during a run. Full supported configuration,
including VLAN membership, is checked by the local coordinator before each stage
and at final cleanup.

## Monitoring and interpretation

The independent recorder collects fresh PDUs on Firewalla eth2 and eth3, native
bond state, interface counters, and kernel events. It continues while the Mac's
network path is absent. A possible native recovery stops subsequent trials after
the current port is restored and its observation finishes. Packet/native evidence
is reviewed before the separate combined launch. Final capture drop counts must
be checked after stopping the recorder.

These short windows look for recovery or a change of behavior. They cannot meet
the existing watcher's five-minute sustained-recovery criterion while a port is
disabled. A transient clean sample is a lead requiring packet confirmation and
repeatability, not proof of a repaired LAG or two-member application throughput.

Removing all three downstream branches tests whether their ongoing traffic is
needed to maintain the existing failure. No recovery would not rule out an earlier
endpoint trigger that left persistent switch/peer state. It also leaves the
Firewalla and any devices on other ports present. At the preceding review, ports
1 and 2 still had the Mac's standalone adapters at 1 Gb/s on VLAN 3999, and only
ports 3 and 4 formed a LAG. The user had reported IoT Wi-Fi restored and all three
APs changed to wired backhaul only; those AP settings were not independently read.

A later startup comparison with selected branches absent, followed by their
controlled restoration, would provide stronger trigger evidence. It would require
a separately scoped runtime reset and observation beyond the previous roughly
237-second post-recovery failure time. That interruption is not included here.

## Operation

Prepare with a new private directory:

```console
.venv/bin/python -m tools.port_isolation prepare --directory backups/port-isolation-new
```

Preparation produces separate `start-individual.command` and
`start-combined.command` files. The combined launcher refuses incomplete
individual trials, possible recovery, or insufficient packet/native coverage.
All launch, result, and evidence files stay in that private directory.

```console
.venv/bin/python -m tools.port_isolation status --directory backups/port-isolation-new
.venv/bin/python -m tools.port_isolation finish --directory backups/port-isolation-new
```

`finish` refuses active test/restoration workers, verifies the full original
configuration, stops and fetches the owned recorder, preserves runtime evidence,
and removes the temporary remote credential copy. An ambiguous launch must be
inspected using its owned unit and journal; never retry it blindly.
