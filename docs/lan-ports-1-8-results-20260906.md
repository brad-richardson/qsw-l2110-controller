# LAN ports 1+8 experiment — September 6, 2026

**Configuration saved and verified at 16:10:33 UTC; physical move and reliability
result pending.** The user was told to move only the QNAP cable end **2 → 8**,
keeping Firewalla eth3 attached and the eth2 cable on QNAP port 1 unchanged.
Firewalla network settings have not been modified.

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

## Monitoring and next actions

A non-promiscuous passive recorder started at **16:05:46 UTC**, bounded to
**1,800 seconds** (roughly **16:35:46 UTC**). It records bond state, link counters,
kernel events, and LACPDUs on eth2/eth3. It remains running at this handoff.
No iperf server or automatic configuration restoration is armed.

Confirm the physical move using fresh reciprocal PDUs identifying eth2→QNAP1
and eth3→QNAP8, both clean in one aggregator with key 1. Do not count the retained
port-2 partner record immediately after configuration as port-8 negotiation.
If both converge, observe 10–15 minutes including a bounded bidirectional
throughput test; if negotiation fails, collect evidence before deciding the next
move. Stop/fetch owned jobs and audit cleanup when measurement finishes.

The user discussed **7+8** as a preferred eventual layout. It is not configured.
A successful 1+8 control would allow the subsequent test to replace only port 1
with port 7 while retaining a tested port 8.

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
