# Guided Linux USB LACP sweep

The runner tests every unordered pair of QNAP ports **1–8**: 28 combinations.
Port 10 stays connected to the unmanaged switch for management. Ports 9 and 10
are outside this experiment. This is an initial negotiation map, not a throughput
or long-term reliability test.

## Current bench readiness — September 6, 2026

Read-only checks after the user removed the port-8 cable confirmed that **only
QNAP port 10 has carrier**, at 2500 Mbps full duplex. The switch at 192.168.1.72
is reachable through this host's `eno1` / 192.168.1.92. Both dedicated USB NICs
are present, administratively down, and have no IP addresses:

| Interface | USB ID | Driver | USB bus speed |
|---|---|---|---|
| `enx5c857e38d8d1` | 0bda:8153 | r8152 | 5000 Mb/s |
| `enxc8a362f421b0` | 0b95:1790 | cdc_ncm | 480 Mb/s |

The second adapter's USB 2.0 connection should be sufficient for LACP packets;
these are **USB bus speeds**, not measured Ethernet negotiation speeds. The
runner requires both Ethernet members to negotiate equal full-duplex speeds.

The actual switch preparation plan was checked without applying it: disable
the old 1+3 LAG and give ports 1–8 untagged VLAN 1/PVID 1. Keep port 10's VLAN 10
and port 9's VLAN 3999 memberships/PVIDs as they are. No Firewalla settings are
changed by this tool. No hardware sweep has been run during implementation.

## Run on bradflix

Keep both USB adapters connected to USB. Initially unplug **all Ethernet cables
from QNAP ports 1–8**. Keep QNAP port 10 connected to the unmanaged switch.
Production traffic must already have its path outside the QNAP test ports.

Run in an interactive terminal:

```bash
cd /home/brad/dev/qsw-l2110-controller
sudo .venv/bin/python -m tools.lacp_sweep run \
  --bench-isolated --insecure --prepare-vlan 1 \
  --management-interface eno1 --management-port 10 \
  --interfaces enx5c857e38d8d1 enxc8a362f421b0 \
  --ports 1-8 --seconds 30
```

Enter your local sudo password in the terminal. The runner reads the existing
QNAP credentials from `.env`; it does not print them. Preparation backs up the
original switch configuration, applies the bench VLAN once, and saves a neutral
bench baseline. Wait for **READY**, then plug the two USB Ethernet cables into
QNAP ports **1+2**.

The runner detects the connected pair, disables both USB members, configures
QNAP group 4 Long, rebuilds a dedicated Linux 802.3ad Slow bond, and records the
trial. Leave cables still during DETECTED/MEASURING. Move only after **NEXT**:

```text
RESULT: 1+2 -> NEGOTIATED (1/28 pairs).
NEXT: keep port 1 connected; move the cable from port 2 to port 3.
```

Follow these instructions through all 28 pairs. No port labels need typing.
Suggested consecutive pairs share one port, so each step takes one cable move.
If a different pair is connected, it is recorded and the next instruction adapts
to the remaining pairs. A failed negotiation still counts as a measured pair;
an interrupted cable move does not. It stops automatically when coverage is
complete. `r` + Enter repeats the current pair between trials; `q` + Enter quits
between trials; Ctrl-C interrupts a measurement and starts cleanup.

Thirty seconds is the measurement window, **not a cable-swap timer**. Reset,
configuration verification, and movement add time. If native state is clean but
fresh reciprocal Slow LACP evidence is pending, the window can extend by up to
35 seconds. Allow roughly 20–40 minutes, depending on API response times and
negotiation. Default bounds are 90 minutes and 50 trials.

## Evidence and interpretation

Each run gets a private `backups/usb-sweep-<UTC>/` directory with:

- `summary.csv` / `summary.json`: pair, classification, elapsed time, time to
  native/reciprocal synchronization, observed USB-to-switch mapping, and evaluation;
- `coverage.json`: completed and remaining pairs; `pair-order.json`: initial order;
- per-member LACP PCAPs and tcpdump logs; `bond-states.txt`: privileged Linux state;
- `linux-counters.jsonl` and `switch-counters.jsonl`: timestamped host/switch samples;
- `events.jsonl`: configuration changes, errors, and trial boundaries;
- `instructions.txt`: a timestamped copy of the concise terminal instructions;
- per-trial switch plans and before/after state; original opaque QSS backup;
- `host-cleanup.json`: capture exit codes, cleanup errors, verified returned NICs.

`NEGOTIATED` requires currently clean native state and fresh reciprocal packet
evidence lasting at least two seconds within the quick window. It does not mean
five-minute stability, data forwarding, or production 2.5 Gb/s behavior passed.
`NATIVE_ONLY_NEEDS_MORE_TIME` means packet confirmation is incomplete.
`NOT_ESTABLISHED_IN_WINDOW` and `NO_PEER_LACP_IN_WINDOW` distinguish unsuccessful
negotiation from missing peer packets. Review tcpdump drop counts before treating
missing captured packets as switch behavior. The runner records cable interruptions
and errors explicitly rather than turning them into successful trials.

Use this pass to choose representative successful and failing pairs for longer
controls. Earlier failures sometimes appeared only several minutes after reboot;
a 30-second success cannot exclude those failures. If even 1+2 fails, inspect the
USB peer setup and captures before attributing the whole matrix to the QNAP.

## Isolation and exit behavior

The two named USB interfaces move into a unique temporary network namespace with
an unaddressed bond. The management route is checked throughout. Startup requires
an empty test pool; exactly two pool links and both USB carriers are required to
start each trial. No other devices should be attached to ports 1–8 during the sweep.
Some USB PHYs retain switch carrier while administratively down: the runner checks
both members' administrative DOWN state and permits only the detected pair to
retain physical link during configuration. It does not treat a powered PHY as
proof that the host interface is forwarding.

Per-trial writes change LAG membership only, with full readback and checks for
unexpected VLAN, port-setting, or mirror changes. An ambiguous response gets a
readback, not a blind write retry. Port 9/10 settings and memberships are preserved.

On normal exit or Ctrl-C, cleanup disables the bench LAG if the observed
configuration still matches the runner's expectations, stops its captures, returns
the two USB NICs to the host with their original MACs and leaves them down. The
prepared bench VLAN is retained; production configuration is **not restored**.
Artifacts are handed back to the invoking sudo user when cleanup finishes. Check
`host-cleanup.json` if the tool reports an error. A killed process, USB removal,
lost management path, or external switch edits can prevent complete cleanup;
inspect recorded state before any recovery operation.

## Resume after interruption

After Ctrl-C, wait for cleanup and the final Results path. Unplug the two test
Ethernet cables from the QNAP again, leaving USB and port 10 connected. Re-run
the same command with the previous directory appended, for example:

```bash
sudo .venv/bin/python -m tools.lacp_sweep run \
  --bench-isolated --insecure --prepare-vlan 1 \
  --management-interface eno1 --management-port 10 \
  --interfaces enx5c857e38d8d1 enxc8a362f421b0 \
  --ports 1-8 --seconds 30 \
  --resume backups/usb-sweep-YYYYMMDDTHHMMSSZ
```

Replace the final path with the actual previous run directory printed at startup.
Each resume creates a **new directory** and records its parent in `resume.json`.
The new summary carries forward measured pairs, including measured failures,
with their original `source_run` paths. Incomplete attempts are retried. It reuses
the Linux actor MAC and validates the adapters, order, test settings, and physical
switch identity. JSON summaries are replaced atomically so interruption cannot
leave a half-written summary. Use the newest directory for another resume.

Resume restores progress, not an arbitrary damaged host state. If a hard kill or
USB removal prevented cleanup, the runner refuses missing/enslaved/addressed NICs
or mismatched MACs. Inspect the preceding `peer.json`, `host-cleanup.json`, and
namespace state before recovery; do not start a second instance over a live one.
A switch configuration change or reboot is also an experimental discontinuity:
the new directory preserves that boundary and takes a new original-state backup.

The implementation has automated coverage for configuration/readback failures,
retained PHY carrier, stale or mismatched packet evidence, Slow-PDU grace,
interrupted results, full 28-pair scheduling, and automatic completion. The full
repository test suite passed (242 tests); the new runner has not yet been exercised
with a real root-owned USB bond.
