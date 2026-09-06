# LAN ports 1+3 experiment — September 6, 2026

**1+3 configuration saved and independently verified at 2026-09-06T16:23:02.680969+00:00. Physical
connection and port-3 negotiation are pending.** The user unplugged the QNAP
cable from port 8 during preparation and was asked to leave it disconnected
until ready. Firewalla configuration and the cable on QNAP port 1 are unchanged.

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

## Physical connection and monitoring

The user should plug the loose QNAP cable end previously on port 8 into **port 3**.
Leave the Firewalla eth3 end and the eth2 cable on QNAP port 1 unchanged.
No successful port-3 negotiation is claimed yet. Check fresh native state and
reciprocal LACPDUs for eth2→QNAP1 and eth3→QNAP3, both clean in one aggregator.
If healthy, observe 10–15 minutes and run a bounded bidirectional throughput test.
If negotiation fails, collect evidence before coordinating the next action.

A non-promiscuous recorder started at **16:21:01 UTC**, bounded to **1,800 seconds**
(roughly **16:51:01 UTC**). It remains running at this handoff. No iperf server
or automatic restoration is armed. Stop/fetch it and audit cleanup when done.

## Fallback

The [dedicated 1+2 restore](../examples/experiments/lan-lag-ports-1-2-restore-from-1-3-20260906.yaml)
can be applied **directly from this 1+3 target**: VLANs already match and only
two LAG membership fields change. Coordinate the cable move **3 → 2** after
verified configuration. No rescue-VLAN stage is needed for this fallback.
Do not apply the 1+8 staging instructions to a direct 1+3→1+2 restoration.

Private evidence and recorder manifest:
`backups/lan-ports-1-3-20260906T162028Z`. Raw captures, credentials, and switch backups remain excluded
from Git. Previous 1+4 and 1+8 results describe failed controls, not this new test.
