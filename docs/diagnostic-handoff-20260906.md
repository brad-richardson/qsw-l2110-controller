# Diagnostic handoff — September 6, 2026

**Latest update at 16:01 UTC: 1+4 failed; switch configuration restored to 1+2.**
Port 4 came up at 2.5 Gb/s but never synchronized in roughly 103 seconds of
recorded post-move traffic: all 103 QNAP PDUs were defaulted with zero partner,
while Firewalla recorded outgoing LACPDUs. Key 1 was retained. Port 1 stayed
healthy. No throughput test was run. The recorder was stopped/fetched and
cleanup verified at 15:59:54 UTC.

The fallback 1+2 configuration was saved and independently verified at
**16:01:22 UTC**, but the cable was still on port 4. A physical **4 → 2** move
and fresh bond read would be needed to restore both members. The user has
since proposed another test; **1+8 followed by 7+8** is recommended to change
one member at a time. Neither is configured, and port 8's rescue VLAN must be
handled before either move. Firewalla settings remain unchanged. No recorder,
iperf server, or automatic restoration is running. Read the
[1+4 execution report](lan-ports-1-4-results-20260906.md) and
[evidence](evidence/lan-ports-1-4-20260906.json) first. The completed 1+2 results
below describe the preceding healthy baseline, not current cable placement.

**The preceding production LAN baseline used QNAP ports 1+2, group 4, Long timeout. Both
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
then told they could make an SFP-related cable swap and subsequently reported
that it took down the network. By **14:44–14:46 UTC**, the gateway, QNAP, and
internet were reachable again, both LACP members were clean, and host/Firewalla
link-failure and hardware-error counters were unchanged. The module, endpoints,
and any physical rollback remain unconfirmed. No post-swap load test ran and
no new server/recorder was started. See the
[baseline and SFP-swap outage report](iperf-sfp-reliability-20260906.md).

The user has now identified the intermediate switch as a **Tenda TEM2010X**.
The user confirmed **Standard mode**, with the copper uplink on **Tenda port 8**
and the server port unknown. They ordered a **10Gtek ASF-10G2-T**, arriving later
this week; the failed module and exact SFP slot remain unidentified. No supported
remote management/logging interface was found. Its Static Aggregation preset
groups **Tenda copper ports 7+8**, not SFP+ 9+10; read the model-specific
inspection findings in that report before changing its mode or cabling.

## Latest read-only check and proposed next control

At **15:18:12 UTC**, the current dated 1+2 target still produced an empty plan.
QNAP ports 1, 2, and 10 were up at 2.5 Gb/s; 4, 7, and 8 had no carrier.
A fresh privileged Firewalla read showed both members at 61/61, 2.5 Gb/s,
aggregator 1, partner key 1, and link-failure counts still 9 each. This is a
later healthy snapshot, not continuous capture covering the intervening period.
No device network configuration was changed.

**Original proposal, subsequently applied at 15:38 UTC:** test QNAP **1+4** before 7+8. Preserve group 4,
Long timeout, VLAN 10, and all Firewalla settings; after coordinated switch
configuration, move only the QNAP end of the eth3 cable **2 → 4**. Retaining
lowest member 1 is expected to retain advertised key 1, which must be verified
in live PDUs. This retests previously failing port 4 while controlling the
lowest-member/key difference between successful 1+2 and failed 3+4. It also
separates the cables physically, although there is no evidence that proximity
or heat caused the LACP failure.

Record native state and reciprocal LACPDUs for at least 10–15 minutes after
convergence, including a bounded bidirectional throughput test and error/link
counters. Restore the current dated 1+2 target and cable placement if it fails;
do not modify Firewalla networking. A pass would argue against a universally
broken port 4 and support dependence on group composition/lowest member; a
failure would leave both port-specific behavior and persistent switch state
as possibilities. Neither outcome by itself proves the hardware fault location.

QNAP 7+8 remains a useful later pair comparison, but changes both members and
likely the advertised key, and requires repurposing **QNAP port 8 from VLAN 1**.
Preserve a rescue-port alternative if scheduling it. This is separate from the
**Tenda uplink on its own port 8**. Earlier port-7/dummy-group tests were not a
controlled live 7+8 production test.

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

## Prior completed 1+2 baseline state (see latest update above)

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
