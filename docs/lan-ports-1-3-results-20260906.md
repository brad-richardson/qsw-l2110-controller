# LAN ports 1+3 experiment — September 6, 2026

**Passed: both members stayed clean for 678.574 seconds under the joint
native-state/fresh-reciprocal-PDU criterion, including bidirectional throughput
at approximately 2.35 Gb/s. Configuration and cables are left on 1+3.**
Firewalla settings are unchanged. The recorder and iperf server are stopped,
and cleanup is verified. No automatic restoration is armed.

Before applying, port 1 was clean at 61/61, 2.5 Gb/s, key 1; eth3 was down with
link-failure count 13 after the user unplugged it. Ports 2, 3, and 8 were empty
at the apply preflight. Observer management remained on port 10.

## Applied configuration

- LACP group **4 on 1+3, Long**; Firewalla remains Slow.
- VLAN 10 untagged/PVID 10: **1,2,3,4,5,6,7,10**.
- Rescue VLAN 1 is back on **port 8**; VLAN 3999 remains on port 9.
- Port settings, mirrors, and unrelated LAG fields were verified unchanged.

To avoid the cyclic VLAN exchange, the controller first applied the existing
[rescue intermediate](../examples/experiments/lan-lag-ports-1-8-rescue-stage-20260906.yaml):
LAG membership temporarily on 1+2, rescue VLAN on 3+8. It then applied the
[1+3 target](../examples/experiments/lan-lag-ports-1-3-long-20260906.yaml), moving
port 3 back to LAN VLAN 10 and replacing LAG port 2 with 3. The user had no
cable on ports 2, 3, or 8 during these writes; port 1 was the only connected LAN LAG member.

Each stage downloaded a private backup and was saved only after matching fresh
readback. VLAN response timeouts were handled by reading the actual state and,
when it matched, requesting save without repeating the VLAN write. Reboot
persistence is untested. Target and fallback configs validate; forward stages
and direct fallback were checked against baseline-derived snapshots.

## Physical connection, LACP, and throughput result

The user connected the loose QNAP end of the Firewalla eth3 cable to port 3.
Both members first reached clean native state at **2026-09-06T16:25:14.089000+00:00**.
The final recorded sample was **2026-09-06T16:37:02.634000+00:00**. Mapping was eth2→QNAP1 and eth3→QNAP3,
2.5 Gb/s per member, actor/partner states 61/61, group 4, advertised key 1,
in a single two-member aggregator. No switch or router reboot was performed.

All **698 native samples over 708.545 seconds** after convergence were clean;
maximum sample gap was 1.103 seconds. The stronger
joint criterion also requires fresh reciprocal LACPDUs, matching identities,
keys, and ports. Its longest and current clean intervals were both
**678.574 seconds**: an earlier pass is not masking a later failure.
Link-failure counts stayed eth2=9 and eth3=13 after convergence.

The same observer/Firewalla benchmark used four TCP streams, 60 measured seconds
per direction after three seconds of warm-up, on TCP port 5209:

| Direction | Receiver throughput | TCP retransmissions | Observed bulk forwarding |
|---|---|---|---|
| Observer → Firewalla | 2.353 Gb/s | 1 | Both eth2/port1 and eth3/port3, approximately 25%/75% of received member bytes. |
| Firewalla → observer | 2.353 Gb/s | 0 | eth3/port3, consistent with the existing layer2+3 hash. |

No zero-byte measured intervals occurred. During load, observer and Firewalla
RX/TX errors, drops, CRC, and missed-error counters showed no increases; every
QNAP port's bad-packet counters also showed zero increase. The observer carrier
counter was unchanged. Firewalla bond identity, members, mode, Slow rate, and
transmit hash matched the load baseline again at final cleanup.

The recorder ran non-promiscuously from 16:21:01 UTC and was explicitly stopped
after the observation window, before its 30-minute bound. Both captures reported
zero kernel drops, and complete PCAP frame counts matched tcpdump's captured
counts. Cleanup at **2026-09-06T16:37:03.896263+00:00** verified both transient units collected/inactive,
MainPID 0, no owned recorder processes, and test port 5209 closed. The local
bounded watcher exited normally. Gateway, switch, and internet probes each
passed 2/2. Explicit recorder stop means there is no natural-expiry marker.

Final live switch readback at **2026-09-06T16:36:57.468933+00:00** matched the saved 1+3 target, with
port settings and mirrors preserved. Raw evidence is private; see
[sanitized results](evidence/lan-ports-1-3-20260906.json).

## Interpretation

This pair passed well beyond the earlier 220–237-second failure window, and
port 3 demonstrably received and transmitted bulk data. The same Firewalla NIC
and cable worked on ports 2 and 3 but failed to synchronize on ports 4 and 8
in the intervening controls. Together these results favor QNAP port-dependent
behavior or persistent switch state over a general Firewalla bond setup error.
They do not establish that specific physical ports are defective, or fully
exclude a Firewalla/peer interaction. No reboot or factory reset cleared switch
state between the port moves.

Leave 1+3 in place for normal observation. This bounded test is not a long-term
reliability guarantee. The observer is limited to 2.5 Gb/s, so it cannot prove
aggregate throughput above one member; reverse bulk forwarding on eth2 was
not exercised by the single endpoint pair. A future reboot remains a separate,
coordinated control. No further change or reboot is scheduled.

## Fallback

The [dedicated 1+2 restore](../examples/experiments/lan-lag-ports-1-2-restore-from-1-3-20260906.yaml)
can be applied **directly from this 1+3 target**: VLANs already match and only
two LAG membership fields change. Coordinate the cable move **3 → 2** after
verified configuration. No rescue-VLAN stage is needed for this fallback.
Do not apply the 1+8 staging instructions to a direct 1+3→1+2 restoration.

Private evidence and recorder manifest:
`backups/lan-ports-1-3-20260906T162028Z`. Raw captures, credentials, and switch backups remain excluded
from Git. Previous 1+4 and 1+8 results describe failed controls, not this new test.
