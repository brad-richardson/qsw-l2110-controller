# Firmware comparison results — 2026-09-05

**The LAN failure reproduced on QSS 2.2.2, 2.2.1, and restored 2.2.3.** Each flash/reboot temporarily restored clean LACP on both production members. QNAP port 4 then advertised a defaulted state almost exactly 237 seconds after its first fully reciprocal clean PDU. Neither older firmware provided sustained recovery. The switch was returned to **2.2.3.20260713** before the subsequent [single-LAG and device-event test](lacp-single-group-and-events-20260905.md).

This was the user-authorized live execution of the [reviewed unattended runner](firmware-unattended-runner-20260905.md), commit `29ba092`. Dates in filenames use the Mac's local date; all times below are **UTC**, crossing into September 6.

## Setup and controls

- QSW-L2110-10T, hardware A0; production LACP group 4 on ports 3+4, VLAN 10. Firewalla `bond0` remained dynamic 802.3ad, Slow, with `eth2` on switch port 3 and `eth3` on port 4. Both physical links were 2.5 Gb/s full duplex.
- Both configured switch LAGs, ports 1+2 and 3+4, used **Long** throughout the firmware comparison. No cables moved and no Firewalla network settings changed. The ONT remained connected directly to Firewalla, with switch port 9 empty.
- Wi-Fi `en0` provided the Mac's default route. QNAP learned its management traffic on ordinary port 6, outside the production LAG. The detached coordinator and router recorder continued without cloud/inference access during reboots.
- Each image passed its pinned hash checks, the switch's final verification response, and an exact post-boot version read. There were no ambiguous uploads or upload retries.
- After every reboot, the complete supported configuration matched the original canonical SHA-256 `84f599868694e27a4abb959608f192b3d2046b29ca8a94a70b11d88cf557d87b`. **No configuration repair or backup import was needed.** This covers LAGs, VLAN membership/names, independent PVIDs, and port admin/speed/flow/EEE settings; it is not a claim about unexposed internal runtime state.

## Measured outcomes

Each measured window lasted approximately 450 seconds. A pass required at least 300 continuous seconds of jointly clean native member state and fresh reciprocal PDUs on both members. Pre-window packets could not establish freshness. This deliberately gives a shorter clean interval than the initial post-boot handshake-to-failure timing below.

| Build and stage | Observation window, UTC | Longest jointly clean interval | Outcome |
|---|---|---:|---|
| 2.2.3.20260713, existing runtime | Sep 5 23:39:04–23:46:34 | 0.000 s | Existing port-4 failure throughout |
| 2.2.2.20260520 | Sep 5 23:57:03–Sep 6 00:04:34 | 207.015 s | Temporary recovery, then failure |
| 2.2.1.20260417 | Sep 6 00:14:59–00:22:30 | 206.858 s | Temporary recovery, then failure |
| 2.2.3.20260713, restored | Sep 6 00:33:07–00:40:38 | 206.763 s | Temporary recovery, then failure |

Every window contained 444 privileged native samples. Final tcpdump counters reported **zero kernel capture drops on both members**. These are finalized results from `result.json`, not provisional journal labels or the harness's potentially stale `SYNCED` state. They measure observed negotiation; they do not prove two-member application forwarding or throughput.

## Packet-level failure sequence

The following times come from Firewalla's member captures. “First clean” requires both actor and partner state to be 61 (`0x3d`), including synchronization, collecting, and distributing, with reciprocal identities. The first bad QNAP PDU has actor state 69 (`0x45`) and a zero partner identity/state. Firewalla subsequently advertises actor state 13 (`0x0d`) and partner state 69.

| Firmware | First fully clean QNAP PDU on port 4 | First defaulted QNAP PDU | Elapsed | Firewalla first bad PDU | Response gap |
|---|---|---|---:|---|---:|
| 2.2.2 | 23:56:47.454717 | 00:00:44.549364 | 237.095 s | 00:00:44.731480 | 182.116 ms |
| 2.2.1 | 00:14:43.414044 | 00:18:40.419069 | 237.005 s | 00:18:40.695535 | 276.466 ms |
| 2.2.3 restored | 00:32:51.901154 | 00:36:48.796286 | 236.895 s | 00:36:48.871593 | 75.307 ms |

QNAP retains its own actor key 3 and port number 4 while forgetting its partner. The native Firewalla member then confirms 13/69. Port 3 remains clean after startup. Physical switch link polling shows no changes during the measured failures; native link-failure counters remain constant within each window, and the kernel log has no bond/member link event within 60 seconds of those transitions.

Sender-side capture does **not** prove that every outgoing PDU reached the wire or the switch receive machine. The first captured defaulted advertisement also does not locate when a receive problem began. These observations establish the visible failure sequence, not which device first loses or misattributes a frame.

## Connectivity and traffic correlation

Wi-Fi gateway and internet probes failed during the expected switch reboot/recovery periods. The first two reboots each produced one approximately 13-second bracket between successful probe observations. The final reboot produced a similar bracket followed by another approximately 9-second bracket. There were no probe failures around the three later LACP default transitions. The continuous one-second monitor started during the first upload, so it does not cover the entire initial baseline/upload; the runner also recorded baseline gateway checks.

Firewalla's existing kernel audit logs, interface counters, archived flow logs, and DHCP/device logs were preserved and aligned with the packet timestamps. None of the three failures had a DHCP exchange within ±15 seconds or a matching FireMain discovery/online/offline event within ±60 seconds. Flow activity differed substantially between the failures. This does not exclude traffic as a trigger, Wi-Fi reassociation without DHCP, or a device event that Firewalla did not log. See the [event analysis](lacp-single-group-and-events-20260905.md) for counts and capture limitations.

## What this establishes

The evidence argues against a regression confined to 2.2.3. It also shows that successful initial negotiation is insufficient evidence of a working setup. Repeatability to approximately 0.2 seconds across three firmware builds makes runtime initialization and protocol timing useful hypotheses.

Every firmware change also rebooted the switch, so this experiment cannot distinguish flashing from rebooting or another runtime-state reset. The preserved public configuration does not reveal internal forwarding or LACP receive state. No factory reset was performed.

The next configuration experiment disabled only the unused first LAG in the already-failed runtime state. A separate boot with only the LAN LAG enabled would be needed to test whether one-LAG initialization behaves differently; disabling it live does not answer that question.

## Durable evidence

The sanitized [machine-readable summary](evidence/firmware-comparison-20260905-summary.json) contains finalized negotiation results, packet transitions, configuration hashes, and SHA-256 references for the private artifacts. The preparation/review record is [also preserved](evidence/firmware-unattended-preparation-20260905-summary.json).

Raw evidence remains ignored under `backups/firmware-unattended-prep-20260905/live-run/`: journal, full snapshots and `.cfg` backups, all stage captures, final member PCAPs, privileged native state, kernel events, interface counters, and Wi-Fi probes. The owned remote LACP recorder stopped normally. Firmware images and SSH material remain private. No report or capture was sent to QNAP or Firewalla.
