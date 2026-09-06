# LAN ports 1+4 experiment — September 6, 2026

**Configuration applied; physical cable move and reliability result pending.**
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
It is bounded to **1,800 seconds**, ending around **16:07:32 UTC** unless stopped
sooner. No iperf server or automatic configuration restoration is armed.
The immediately post-apply native snapshot still identified partner port 2;
that retained partner record is not evidence of port-4 negotiation.

Next: confirm eth2→QNAP1 and eth3→QNAP4 in fresh reciprocal PDUs, both clean
in one aggregator, and verify advertised key 1. Observe at least 10–15 minutes
after convergence, including bounded bidirectional throughput testing, then
fetch evidence and audit recorder/server cleanup. Do not alter Firewalla's LAG.

Restore with the [current 1+2 target](../examples/experiments/lan-lag-ports-1-2-long-20260906.yaml)
and a coordinated cable move **4 → 2**. Observer management stays on QNAP
port 10 and the rescue VLAN stays on QNAP port 8 throughout.

Private evidence and recorder manifest:
`backups/lan-ports-1-4-20260906T153606Z/`; raw captures and backups are excluded
from Git. The old 1+2 report describes the prior completed test, not the currently
applied switch membership.
