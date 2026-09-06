# Linux USB port sweep — September 6, 2026

## Result: 28/28 pairs completed; four negotiated

**1+2, 1+3, 1+7, and 2+3 negotiated. The other 24 pairs did not establish in
their measurement windows.** This is an initial negotiation map, not a throughput
or long-term reliability result. No failed pair subsequently reached clean native
state during the recorded hold before its first observed cable/link change.

Two RTL8153/r8152 USB adapters on bradflix supplied an independent Linux peer at
1000 Mb/s full duplex. QNAP QSW-L2110-10T firmware 2.2.3.20260713 used group 4
Long; Linux used Slow and the same actor identity across the resumed run. Only
one LAG was active. Ports 1–8 had untagged/PVID VLAN 1; port 10 retained management
and ports 9–10 were excluded. Firewalla configuration was not changed.
The preceding 90-second 1+3 control validated this USB peer; earlier ASIX attempts
are invalid port-pair comparisons.

## Pair matrix

**Y** = negotiated; **N** = not established in the trial window.

| Port | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | — | Y | Y | N | N | N | Y | N |
| 2 | Y | — | Y | N | N | N | N | N |
| 3 | Y | Y | — | N | N | N | N | N |
| 4 | N | N | N | — | N | N | N | N |
| 5 | N | N | N | N | — | N | N | N |
| 6 | N | N | N | N | N | — | N | N |
| 7 | Y | N | N | N | N | N | — | N |
| 8 | N | N | N | N | N | N | N | — |

Ports 4, 5, 6, and 8 negotiated with none of their seven partners in this sweep.
Port 7 negotiated only with 1. All three pairings within 1–3 negotiated.

## What the labels mean

- `NOT_ESTABLISHED_IN_WINDOW`: switch-origin LACP was captured on both USB NICs
  during the trial, but the two-member bond never reached clean native state.
- `NO_PEER_LACP_IN_WINDOW`: at least one USB NIC had no captured switch-origin
  LACP during that window. The other NIC may have received it. This does not mean
  both physical links were down or that no packets arrived later.

The 15-second setting extends native-clean pairs for fresh Slow-LACP packet
confirmation, so successful trials took approximately 34–36 seconds. Packet
cadence can change which negative label is recorded without indicating a different
root cause. Several no-peer cases received switch LACP after the short window but
remained unsynchronized. The report groups both negative labels as N while keeping
exact classifications in the evidence.

## Timing and extended holds

The table preserves physical test order. “Observed through” measures from trial
start to the last saved sample before a member went down, its link-failure count
changed, or membership changed. These opportunistic holds are not separate timed
trials. The last pair had no post-window hold because cleanup began immediately.

| Pair | Result | Trial (s) | Observed through (s) |
| --- | --- | ---: | ---: |
| 1+2 | Negotiated | 33.7 | 46.3 |
| 1+3 | Negotiated | 35.7 | 44.6 |
| 1+4 | Not established | 15.6 | 37.4 |
| 1+5 | Not established | 15.6 | 55.7 |
| 1+6 | Not established | 15.7 | 66.7 |
| 1+7 | Negotiated | 33.7 | 95.6 |
| 1+8 | Not established | 15.6 | 97.7 |
| 2+8 | Not established | 15.6 | 35.6 |
| 2+7 | Not established | 15.6 | 53.9 |
| 2+6 | Not established | 15.6 | 39.2 |
| 2+5 | Not established | 15.7 | 112.3 |
| 2+4 | Not established | 15.7 | 81.3 |
| 2+3 | Negotiated | 33.6 | 70.0 |
| 3+4 | Not established | 15.6 | 37.4 |
| 3+5 | Not established | 15.7 | 169.0 |
| 3+6 | Not established | 15.6 | 28.3 |
| 3+7 | Not established | 15.6 | 33.7 |
| 3+8 | Not established | 15.6 | 37.4 |
| 4+8 | Not established | 15.6 | 52.0 |
| 4+7 | Not established | 15.6 | 81.3 |
| 4+6 | Not established | 15.6 | 28.3 |
| 4+5 | Not established | 15.7 | 128.8 |
| 5+6 | Not established | 15.7 | 117.9 |
| 5+7 | Not established | 15.7 | 108.7 |
| 5+8 | Not established | 15.6 | 42.9 |
| 6+8 | Not established | 15.6 | 28.2 |
| 6+7 | Not established | 15.6 | 33.8 |
| 7+8 | Not established | 15.6 | 15.6 |

1+7 remained natively synchronized through approximately 96 seconds after trial
start. Negative holds included 1+8 through 97.7 seconds, 2+5 through 112.3 seconds,
3+5 through 169.0 seconds, and 4+5 through 128.8 seconds. No late native-clean
sample was found on a failed pair within the observation boundaries above.
The evidence includes extended reciprocal evaluations for the four successful
pairs, distinct from their shorter recorded trial evaluations.

## Interpretation and next discriminating tests

The independent USB peer reproduces the earlier Firewalla failures on 1+4,
1+8, and 3+7. This strongly favors investigating QNAP-side LACP behavior over a
Firewalla-only explanation. It does not establish physical port damage: firmware,
configuration history, port mapping, and peer/speed interaction remain possible.

**1+7 working disproves a simple “LACP only works on ports 1–3” rule.** Its failures
with 2 and 3 suggest pairing or configuration-state dependence. A simultaneous
LAG-count limit does not explain these trials because only one group was active.
The single sweep order cannot separate pair dependence from persistent state.

The [earlier Firewalla notes](firewalla-topology.md) really did test **3+7** at
2.5 Gb/s and observe the second member defaulted with zero partner identity.
Today's 3+7 also failed its quick window with a different peer, speed, and VLAN.
The older 4+7 outage test lacked protocol capture after the move; today's 4+7
provided a recorded negative trial and an approximately 81-second native hold.

Useful follow-ups, in order:

1. Repeat **1+7 → 2+7 → 1+7**, holding each for at least 90 seconds with the same
   NIC/cable assignments and settings. This checks repeatability and history.
2. After a verified positive control, compare a failing pair before and after a
   bench switch reboot, then hold for at least five minutes. Earlier production
   experiments showed delayed failure after reboot, so a brief pass is insufficient.
3. If needed, compare the same pair in a different LAG group and swap the two USB
   NIC/cable assignments. Keep other settings fixed to separate group, port, and
   peer-member effects. These follow-ups have not been executed by this report.

## Candidate simultaneous pair set: 1+7 and 2+3

These two successful pairs do not overlap, making them a plausible candidate
for two simultaneous LAGs. **They have only been tested individually in group 4.**
The sweep does not establish that they can coexist, that another group assignment
works, or that 1+7 works at the intended 2.5 Gb/s. First repeat 1+7 after the sweep;
then validate both groups concurrently with four working NIC connections across
two peers, at least five minutes of reciprocal evidence, and traffic through both
members of each group. This combined test has not been performed.

## Interruption, artifacts, and final state

The first run, `usb-sweep-20260906T183251Z`, recorded 16 pairs and stopped during
the move toward 3+7 on `GET /port_setting_load.json failed`. It did not record a
3+7 trial. Switch neutralization and host cleanup verified. Resume into
`usb-sweep-20260906T190101Z` preserved all 16 results and the Linux actor identity,
then recorded the remaining 12 pairs without another fatal error.

At **19:19:52 UTC**, final switch cleanup verified the neutral bench LAG state;
the saved bench VLAN remained in place. Both USB interfaces returned down with
original MACs and no host cleanup errors. Both runs' tcpdump processes exited 0;
the owned link monitors were interrupted as part of cleanup. Do not resume this
completed sweep to repeat all pairs; a new controlled test is the next step.

All four PCAP files ended on complete record boundaries, every captured frame
was decoded as LACP, and kernel capture drops were zero. Capture frame counts
were 622/767 in the first run and 899/877 in the resumed run. Sampled QNAP
`TxBadPkt` and `RxBadPkt` values were zero throughout for all ten ports. These
are capture integrity and sampled counter checks, not proof of data forwarding.

[Sanitized evidence](evidence/linux-usb-sweep-20260906.json) preserves exact trial
classifications, native states, mappings, timing, run provenance, capture checks,
and cleanup. Raw backups and packet captures remain local under `backups/`.
