# Diagnostic handoff — September 6, 2026

**The production LAN now uses QNAP ports 1+2, group 4, Long timeout. Both
Firewalla members remained clean at 2.5 Gb/s for 10 minutes 45 seconds under
the joint native-state and fresh-packet criterion.** Firewalla's existing Slow
bond configuration was unchanged. The earlier port-4 failure remains unexplained;
ports 1+2 are the current working workaround, with longer observation still needed.

Latest cleanup was verified at **14:35:01 UTC** after a throughput baseline:
the iperf server and passive recorder are stopped, with no owned process or
restoration armed. Read the
[ports-1+2 execution report](lan-ports-1-2-results-20260906.md) and its dated
target/restore YAML before changing anything. This handoff supersedes the older
MacBook baseline and preparation instructions. Refresh live state before another
hardware test.

**Subsequent throughput baseline:** four TCP streams sustained **2.35 Gb/s in
each direction** for 60 seconds each, with no observed link/LACP regression or
hardware error-counter increases. Upload used both Firewalla members; reverse
traffic used eth3, consistent with the existing transmit hash. The user was
then told they could make an SFP-related cable swap. Its endpoints and new path
are not yet verified; refresh topology before the next test. See the
[baseline and pending SFP test](iperf-sfp-reliability-20260906.md).

## Completed evidence

| Experiment | Result and report |
|---|---|
| Firmware comparison: 2.2.2, 2.2.1, restored 2.2.3 | Port 4 failed about 237 seconds after clean negotiation on every build, with two LAGs configured at boot. [Report](firmware-comparison-results-20260905.md) |
| Disable the unused LAG; isolate IoT Wi-Fi | Neither cleared the existing failure. [Single-LAG report](lacp-single-group-and-events-20260905.md), [Wi-Fi report](lacp-iot-wifi-isolation-20260905.md) |
| Disable downstream ports 10, 5, and 6 individually, then together | No recovery during roughly 60-second individual cuts or the roughly 120-second combined cut. All ports restored. Early port-10 aborts were harness bugs; completed trials followed the fixes. [Report](port-isolation-results-20260905.md) |
| Ordinary reboot with only one LAG, ingress mirror to the Mac | Port 4 failed about 220 seconds after clean negotiation. Its LACP disappeared from the mirror about 93 seconds before QNAP defaulted its partner, while Firewalla continued recording outgoing PDUs. Both working-port controls passed; the final control used a separate capture after a cleanup error. [Report](mirror-reboot-results-20260906.md) |
| Move the existing LAN bond from QNAP 3+4 to 1+2 | Both members clean at 2.5 Gb/s for 644.570 seconds with native state and fresh reciprocal packets; 672.812 seconds of clean native state. Same Firewalla bond/NICs/cables, Slow timeout, and downstream branches. Port 1 came up first. Left on 1+2. [Report](lan-ports-1-2-results-20260906.md) |

The second configured LAG is not necessary for this failure. The branch cuts
tested whether removing ongoing downstream traffic clears an existing failure;
they do not exclude an earlier device trigger leaving persistent state. Branches
were connected during the reboot. Sender-side capture and switch ingress
mirroring do not conclusively locate the fault on the physical wire, Firewalla,
or inside QNAP. All captures in the completed isolation and mirror tests ended
with zero kernel drops; switch-side loss remains outside that measurement.

## Final verified state

- QNAP QSW-L2110-10T, QSS **2.2.3.20260713**. Only LACP group 4 on ports
  **1+2**, Long timeout; Firewalla bond0 still uses Slow LACP.
- Firewalla **eth2 → QNAP 1; eth3 → QNAP 2**. Both clean at native
  actor/partner **61/61**, 2.5 Gb/s, in the same two-member aggregator at the
  last recorded sample, **14:15:55.657 UTC**.
- VLAN 10 untagged/PVID 10 on **1, 2, 3, 4, 5, 6, 7, 10**. VLAN 1 on
  **8**; VLAN 3999 only on **9**. No tagged members. Ports 3+4 are now ordinary
  LAN access ports with no cable.
- Downstream ports **5, 6, and 10 are enabled**, with 2.5 Gb/s carrier.
  Ports 7, 8, and 9 have no carrier; the ONT connects directly to Firewalla.
- The Mac Ethernet cables were removed from ports 1+2 before the LAN move.
  Those ports are now occupied by Firewalla; do not reconnect Mac NICs there.
  Mac software settings were not revisited in this run.
- **All mirror ingress and egress sources are off**; the inactive destination
  selector remains port 7. The prior Mac agent reported restoring en9 DHCP
  with a blank client ID and automatic IPv6 at its 12:38 UTC checkpoint.
- Owned capture/coordinator/cleanup jobs have stopped. Owned temporary router
  credential copies were removed. No pending timer or receiver needs resuming.

The **earlier 12:38 UTC configuration, before the move**, matched SHA-256
`095e9b619f7d91c3a3a998906f9d812e538c0342660f0a79c365b0895f433c9c`.
That hash is historical and is not the current ports-1+2 configuration. The
dated target YAML now produces an empty plan against fresh readback. The user
reported restoring IoT Wi-Fi and using wired AP backhaul; those AP settings were
not independently verified.

## Resume on another machine

Update an existing checkout with `git pull --ff-only`, preserving local changes,
or clone the repository. From its root, install and check the code:

```console
uv venv --python 3.14
uv sync --locked --extra dev
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
```

At the earlier Mac-agent checkpoint, all **208 tests passed**, along with lint and formatting
checks using the locked development environment. The tests use a local HTTP
emulator and fixtures; they do not operate the switch.
Git contains the reusable tools, tests, reports, and sanitized JSON evidence in
`docs/evidence/`. It deliberately excludes `.env`, SSH private keys and pinned
host files under private run directories, configuration backups, firmware,
packet captures, local launchers, and the prior Codex session history.

The new ports-1+2 raw artifacts are on the Linux observer under ignored
`backups/lan-ports-1-2-20260906T135419Z/`. Its sanitized evaluation is committed
in `docs/evidence/lan-ports-1-2-20260906.json`.

Earlier raw artifacts remain on the original Mac under ignored directories:

- `backups/port-isolation-run-20260906T030616Z/`: completed branch cuts.
- `backups/mirror-reboot-run-20260906T121056Z/`: main mirror/reboot run;
  `combined-analysis.json` joins its evidence to the completed final control.
- `backups/mirror-final-control-20260906/`: separate final control and cleanup.
- `backups/mirror-reboot-prep-20260906/`: completed preparation, local launcher,
  and receivers; `COMPLETE.json` records completion. Receiver 3 holds the main
  capture and receiver 4 the final control.

Further raw-packet analysis needs a separate private transfer of those files.
Live access needs local switch credentials and verified router SSH credentials
and host identity; recreate or privately transfer them rather than adding them
to Git. Read the existing evidence before starting another experiment.

Old plans and launchers describe the original machine and completed runs. They
are not an armed handoff. Refresh device identity, exact configuration, adapter
names/MACs, port mapping, independent management route, and restoration readiness
before preparing a new run. The mirror coordinator still targets en9 → port 2
and the documented firmware/topology. Its private plan now requires
`receiver_mac`, matching the receiver helper's `--expected-mac` and fresh
MAC-table evidence; a different machine needs its own verified preparation.
The Mac receiver helper runs on stock Python 3.9+ and still requires local sudo
for packet capture and temporary addressing changes.

## Code included with these results

- `tools/lan_lacp_watch.py` reports the current clean interval, so an earlier
  five-minute success cannot mask a later failure.
- `tools/port_isolation.py` and `tools/port_isolation_agent.py` provide scoped
  individual/combined branch cuts with independent router-side restoration.
  Their fixes separate runtime LAG flags from configuration, accept a successful
  empty write response, and avoid the post-result journal argument collision.
- `tools/mirror_receiver_macos.py`, `tools/mirror_reboot.py`, and
  `tools/mirror_cleanup_agent.py` provide bounded capture, a controlled reboot
  sequence, and independent mirror cleanup. Fixes verify actual IP removal,
  preserve full process command lines during readiness checks, use fresh
  authentication for mirror cleanup, and verify inactive transient units before
  removing temporary credentials.

The main run's original cleanup errors remain in its evidence. Fresh cleanup
and a separate final control resolved them without another reboot; see the
[full execution and cleanup record](mirror-reboot-results-20260906.md).
