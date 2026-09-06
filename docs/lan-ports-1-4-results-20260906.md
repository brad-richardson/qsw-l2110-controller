# LAN ports 1+4 experiment — September 6, 2026

**Failed: port 4 never synchronized in the recorded post-move interval.**
The switch configuration was restored to 1+2 at 16:01 UTC. After the user moved
the cable back, a **16:04:58 UTC** read confirmed both members recovered at
61/61, 2.5 Gb/s, key 1, with eth3 identifying QNAP port 2.
The user authorized the separated-pair control after reviewing the proposal.

Preflight confirmed that QNAP port 4 had no carrier, the current dated 1+2
configuration matched live state, and both Firewalla members were clean.
Only two fields changed: port 2 LACP membership disabled (`portTypeId_2` 2→0),
port 4 LACP membership enabled (`portTypeId_4` 0→2). Group 4 and Long timeout
were retained. No VLAN, PVID, port, mirror, or Firewalla network setting changed.
A private switch configuration backup was downloaded before applying.

The normal controller requested save and verified running configuration.
An independent readback at **15:38:24 UTC** matched the
[target YAML](../examples/experiments/lan-lag-ports-1-4-long-20260906.yaml),
with unrelated settings preserved. Reboot persistence has not been tested.
The user was told to move only the **QNAP cable end 2 → 4**, keeping the
Firewalla eth3 end and the eth2 cable on QNAP port 1 unchanged.

A non-promiscuous passive recorder started on Firewalla at **15:37:31 UTC**,
collecting native bond state, link counters, kernel events, and eth2/eth3 LACPDUs.
It was bounded to **1,800 seconds** and explicitly stopped/fetched at
**15:59:05 UTC** after the failure. No iperf server was started.
The immediately post-apply native snapshot still identified partner port 2;
that retained partner record is not evidence of port-4 negotiation.

## Observed result

The physical move occurred much later than configuration apply. Do not count
the old port-2 partner expiration while waiting for the move as a port-4 failure.

| UTC | Observation |
|---|---|
| 15:39:15–18 | The old port-2 partner record expired after port 2 left the LAG, before the physical move. |
| 15:57:14.825 | eth3 carrier down during the physical move; link-failure count 9→10. |
| 15:57:21.261 | First captured QNAP port-4 LACP PDU: key 1, state 69, zero partner identity. |
| 15:57:21.960 | Native eth3 carrier back at 2.5 Gb/s. |
| 15:57:24.022 | eth3 settled at actor/partner 13/69, identifying QNAP port 4/key 1. |
| 15:59:04.657 | Final recorded sample: port 4 still 13/69; port 1 clean at 61/61. |

All **103 captured QNAP port-4 PDUs** advertised state 69 with a zero partner.
Firewalla recorded **106 outgoing eth3 LACPDUs** after the first port-4 PDU.
Port 4 never reached clean negotiation in roughly **103 seconds** of recorded
post-link traffic. The joint native/fresh-reciprocal-PDU evaluator measured
**zero clean seconds**, not a successful interval too short for its threshold.
Port 1 stayed clean in all post-move native samples. The only eth3 carrier
failure-count increase was the move itself; eth2 remained at 9.

No load test was run because LACP was already degraded. Gateway, switch, and
internet probes each passed 2/2. Both packet captures reported zero kernel
drops, and complete PCAP frame counts matched tcpdump's captured counts.
Recorder cleanup at **15:59:54 UTC** confirmed the unit collected/inactive,
MainPID 0, and no owned processes. Explicit stop means no natural-expiry marker.
See [sanitized evidence](evidence/lan-ports-1-4-20260906.json).

## Interpretation

Retaining lowest member 1 and advertised key 1 did **not** make port 4 work.
This weakens the simple explanation that any group beginning at port 1 is
healthy. It does not prove port 4 is physically defective: the switch was not
rebooted or factory-reset, so persistent per-port/trunk state remains possible.
Sender-side capture does not establish that Firewalla packets reached QNAP's
physical ingress. The same NIC/cable worked on port 2 before the move, making
a general Firewalla bond configuration problem less likely.

The next proposed sequence is **1+8, then 7+8** if 1+8 passes. The first tests
port 8 with the known-working port 1; the second retains tested port 8 while
replacing port 1 with port 7. Going directly to 7+8 is a valid practical layout
test, but changes both members at once. Neither new target is applied. Port 8
currently belongs to VLAN 1; plan LAN membership and a replacement rescue port
before using it. The Tenda's own uplink port 8 is unrelated.

## Fallback and physical return confirmed

After collecting the failure, the controller backed up and restored only the
two membership fields to the known-working 1+2 target. Save and independent
readback verified it at **16:01:22 UTC**, with port/mirror settings preserved.
Port 2 had no carrier before restoring. Port 1 remained 61/61 afterward;
the cable was still on port 4 and its retained failed partner record is not
evidence of a restored two-member bond. No Firewalla setting was changed.
At **16:04:58 UTC**, after the physical return, both members were 61/61 at
2.5 Gb/s with key 1. eth3 identified QNAP port 2 and its link-failure count was
11, reflecting the return cable move; eth2 remained at 9. Recovery occurred
without a switch reboot or Firewalla configuration change. A subsequent 1+8
experiment is tracked separately; check the latest handoff for active jobs.

Restore with the [current 1+2 target](../examples/experiments/lan-lag-ports-1-2-long-20260906.yaml)
and a coordinated cable move **4 → 2**. Observer management stays on QNAP
port 10 and the rescue VLAN stays on QNAP port 8 throughout.

Private evidence and recorder manifest:
`backups/lan-ports-1-4-20260906T153606Z/`; raw captures and backups are excluded
from Git. The old 1+2 report describes the prior completed healthy test. The later
16:04:58 read confirms recovery after this experiment, as a snapshot rather
than a new sustained reliability measurement.
