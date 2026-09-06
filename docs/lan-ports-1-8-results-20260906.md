# LAN ports 1+8 experiment — September 6, 2026

**Failed: port 8 never synchronized during roughly 103 seconds of recorded
post-link traffic.** Port 1 remains healthy and the network reachable. The
configuration and cables are **left on 1+8**, group 4 Long, rescue VLAN on port 3.
Firewalla settings are unchanged. No reboot or restoration was performed after
this failure. The passive recorder is stopped and cleanup verified.

## Recovered baseline before applying

After the failed 1+4 control and configuration fallback, the user moved the
cable back to port 2. At **16:04:58 UTC**, ports 1+2 were both 61/61 at 2.5 Gb/s,
key 1, in one aggregator. Port 4 had no carrier. Link-failure counts were eth2=9
and eth3=11, the latter increment reflecting the physical return.

A new passive capture established **128.048 seconds continuously clean** under
the joint native-state/fresh-reciprocal-PDU criterion before the new experiment.
The evaluator's `pass: false` reflects its five-minute minimum, not a regression:
current and longest clean intervals are equal. This was a brief recovery control,
not another sustained reliability test. The same Firewalla NIC and cable failed
on port 4 and recovered on port 2 without a switch reboot or router changes.

## Applied configuration and staging

- LACP **group 4 on ports 1+8, Long**; Firewalla remains Slow.
- VLAN 10 untagged/PVID 10: **1, 2, 4, 5, 6, 7, 8, 10**.
- Rescue VLAN 1 moved from port 8 to **port 3**; both were empty at preflight.
- VLAN 3999 remains only on port 9. No tagged members.
- Port settings, mirrors, unrelated LAG fields, and management on port 10 preserved.

The first direct plan was rejected before any writes because exchanging untagged
ports 3 and 8 creates a VLAN ordering cycle. The applied sequence was:

1. [Intermediate rescue stage](../examples/experiments/lan-lag-ports-1-8-rescue-stage-20260906.yaml):
   retain LAG 1+2 and add port 3 to rescue VLAN 1, temporarily leaving both 3+8
   there. This changes two VLAN memberships and port 3's PVID.
2. [Final 1+8 target](../examples/experiments/lan-lag-ports-1-8-long-20260906.yaml):
   remove port 2 from LACP; enable port 8 in group 4 with Long timeout; move port
   8 to LAN VLAN/PVID 10. Four LAG fields, two VLAN memberships, and one PVID change.

Both stages downloaded private configuration backups. Each VLAN POST returned a
read timeout, but independent fresh readbacks matched the intended stage exactly.
No VLAN write was repeated. Save was requested only after that verification,
followed by another matching readback. Reboot persistence is untested.
Both staged forward and reverse plans were validated against snapshots derived
from the live baseline. The final applied state was independently verified.

## Recorded move and result

| UTC | Observation |
|---|---|
| 16:10:05–08 | Port 1 briefly changed to 13/69 during configuration apply, then recovered to 61/61 before the physical move. |
| 16:11:18–21 | The old port-2 partner record expired after removing that port from the LAG, before moving its cable. |
| 16:11:56.033 | eth3 carrier down during the physical move; link-failure count 11→12. |
| 16:12:00.340 | First QNAP port-8 PDU: advertised key 1, actor state 69, zero partner. |
| 16:12:01.101 | Native eth3 carrier back at 2.5 Gb/s. |
| 16:12:03.131 | eth3 settled at 13/69, identifying QNAP port 8/key 1. |
| 16:13:43.768 | Final recorded sample: port 8 still 13/69; port 1 clean at 61/61. |

All **104 captured QNAP port-8 LACPDUs** advertised state 69 with a zero partner
identity. Firewalla recorded **107 outgoing eth3 LACPDUs** after the first port-8
PDU. There were **zero clean seconds** under the joint native/fresh-packet
criterion. Port 8 never synchronized, rather than first passing and later
regressing. Observation from the first port-8 PDU to the final native sample was
**103.428 seconds**. Port 1 was clean in every post-move native sample.
Link-failure counts remained eth2=9 and eth3=12 after the cable move.

The target configuration still matched fresh live readback at **16:13:17 UTC**;
ports 1+8 and management port 10 had 2.5 Gb/s carrier, ports 2+3 had none.
No iperf server was started because LACP negotiation had already failed.
Gateway, switch, and internet probes each passed 2/2 after capture cleanup.

A non-promiscuous recorder ran from **16:05:46 UTC** until explicit stop/fetch
at **16:13:44 UTC**, before its 1,800-second bound. Both captures had zero
kernel drops; complete PCAP frame counts matched tcpdump's captured counts.
Cleanup at **16:14:38 UTC** confirmed the unit collected/inactive, MainPID 0,
and no owned processes. There is no natural-expiry marker for this explicit stop.

An initial attempt to fetch over the earlier baseline directory refused with
`FileExistsError`. The recorder was then stopped, the owned baseline copy was
renamed and preserved, and the final capture fetched successfully. No evidence
was overwritten or discarded to resolve that local collection error.

## Interpretation and remaining control

Physical separation and retaining lowest member/key 1 did not make port 8
work. Alongside the same NIC/cable failing on port 4 and recovering on port 2,
this favors a QNAP port/state-dependent issue over a general Firewalla bond
configuration error. It does not identify the physical fault location: Firewalla
sender-side captures do not prove delivery to QNAP ingress, and no switch reboot
or factory reset was performed between these port moves.

**A reboot with the current 1+8 configuration saved is the proposed next control,
not performed or scheduled.** It would distinguish immediate startup behavior
from persistent state left by earlier experiments. If port 8 initially converges
and later fails, that would resemble the earlier 3+4 reboot results. It could
interrupt the whole LAN and must be coordinated with the user.

The user's preferred **7+8** layout remains untested and is not configured.
The intended 1+8-then-7+8 comparison no longer starts with a healthy port-8
control, so another immediate move would be harder to interpret. No timed
restoration is armed. Leave cable placement unchanged until coordinating either
the next test or the staged restoration below.

## Restoration

Coordinate with the user and current cable placement. Apply the **intermediate
rescue stage first**, then the
[dedicated 1+2 restore](../examples/experiments/lan-lag-ports-1-2-restore-from-1-8-20260906.yaml),
and move the QNAP cable end **8 → 2**. This restores rescue VLAN 1 to port 8
and LAN VLAN 10 to port 3. Ensure no new rescue client has been attached to port 3.
The older 1+2 target does not manage port 8; use this dedicated staged restore.
No restoration is armed or scheduled, and no Firewalla changes are needed.

[Sanitized evidence](evidence/lan-ports-1-8-20260906.json). Private run directory:
`backups/lan-ports-1-8-20260906T160541Z/`, including recorder manifest and snapshots.
Raw captures, configuration backups, and credentials stay outside Git.
