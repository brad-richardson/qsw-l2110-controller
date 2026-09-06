# Production LAN LAG moved to ports 1+2 — September 6, 2026

The existing Firewalla LAN bond successfully joined QNAP ports **1+2 at
2.5 Gb/s per member** after moving the switch ends of the same two cables.
Both members remained clean for **10 minutes 45 seconds** under the joint
native-state and fresh-packet criterion, beyond the earlier roughly four-minute
failure window. Firewalla configuration was unchanged. **The LAN is left on
1+2; the passive recorder is stopped and no restoration is armed.**

## Configuration and physical move

The switch was QSW-L2110-10T hardware A0, QSS **2.2.3.20260713**. Before the
move, only group 4 on ports 3+4 was enabled, with Long timeout. Firewalla eth2
was clean at 61/61; eth3 had the existing 13/69 failure. Both had 2.5 Gb/s
carrier. Firewalla's existing `bond0` used Slow LACP.

The Mac Ethernet cables were already removed from ports 1+2 before applying.
The observer's independent switch-management path entered port 10, which was
preserved. The ONT remained directly attached to Firewalla, with port 9 empty.

- Applied [the dated Long target](../examples/experiments/lan-lag-ports-1-2-long-20260906.yaml):
  move **group 4**, retaining Long timeout, from 3+4 to 1+2; move 1+2 from
  untagged VLAN 3999 to untagged VLAN/PVID 10. Ports 3+4 remain ordinary LAN
  access ports after leaving the LAG.
- The reviewed plan had ten changes: six LAG fields, two VLAN memberships,
  and two resulting PVID changes. Unrelated LAG configuration, all port
  settings, and all mirror settings were preserved.
- After verified apply/save, the user moved **QNAP 3 → 1 and QNAP 4 → 2**.
  Firewalla ends stayed attached: **eth2 → 1; eth3 → 2**.
- No Firewalla bond, NIC, timeout, or vendor-managed configuration was changed.
  The existing bond identity and member order were retained. No switch reboot
  or factory reset was performed.

The first local apply attempt stopped before any write because the private
preflight compared a tuple with its JSON list representation. After correcting
that comparison, the normal controller took a configuration backup and applied
the reviewed plan. The VLAN POST returned an HTTP read timeout. A fresh session
then found **zero remaining changes**, so the write was not repeated. Explicit
save and another readback confirmed the complete target at **14:03:34 UTC**.
The later physical move happened only after this verification.

## Observation

All times are UTC. The recorder was started before applying, with one-second
native bond snapshots, link counters, kernel events, and non-promiscuous LACP
captures on eth2 and eth3. It did not mirror switch traffic or modify router
network settings.

| Time | Observation |
|---|---|
| 14:04:25.604 | eth2 first recorded down during the cable move. |
| 14:04:27.633 | Both members recorded down. |
| 14:04:35.745 | eth2 regained 2.5 Gb/s carrier on port 1. |
| 14:04:37.769 | eth2 clean at 61/61, identifying switch port 1. |
| 14:04:39.797 | eth3 regained 2.5 Gb/s carrier. |
| 14:04:42.845 | Both native members clean at 61/61 in one two-member aggregator, identifying ports 1+2. |
| 14:05:11.087 | Start of the continuously clean interval requiring native state and fresh reciprocal LACPDUs together. |
| 14:15:55.657 | Last recorded native sample: both members still clean at 61/61, 2.5 Gb/s, aggregator 1. |
| 14:16:30.702 | Recorder cleanup verified: inactive unit, main PID 0, no owned processes. |

Port 1 came up first in this run. This is one bring-up order, not the complete
two-order procedure in the older runbook. The packet criterion is stricter
than a native-state screenshot: all four actor/partner state bytes must be
clean, both directions must report matching identities, keys, and ports, the
latest PDU from each peer must be at most 35 seconds old, and native samples
must be at most 2.5 seconds old. Startup convergence precedes the measured
continuous interval.

The completed capture contains **672.812 seconds** of continuously clean
native state across 663 samples, including **644.570 seconds** continuously
passing the joint criterion. The current and longest joint clean intervals
are equal; an earlier success is not masking a later failure. Link-failure
counters rose from 8 to 9 on each member during the physical move and remained
constant after the first clean sample.

Gateway, switch, and internet ICMP probes each passed **9/9** between 14:10:47
and 14:14:47. Both tcpdump logs reported **zero packets dropped by kernel**.
Fresh switch readback and privileged router reads at about 14:15:33 confirmed
the target configuration, preserved switch settings, unchanged bond identity
and settings, and both clean members. The earlier unprivileged post-check read
omitted Linux's privileged LACP fields; it was repeated with read-only sudo
before judging the final state.

The recorder was explicitly stopped early after the observation window, then
fetched. Its transient unit had been collected and no process remained in the
run directory. There is no natural-expiry `finished.txt` for this explicit stop.
The [sanitized machine-readable evidence](evidence/lan-ports-1-2-20260906.json)
records timing, member state, probe counts, capture counters, and cleanup.

## Interpretation and remaining controls

This run passed the approximately **220–237 second** failure window seen on
ports 3+4 with the same restored firmware. The previous
[single-LAG mirror/reboot experiment](mirror-reboot-results-20260906.md) and
[firmware comparison](firmware-comparison-results-20260905.md) record those
failures.

Keeping the Firewalla LAN bond, NICs, cables, speed, Slow timeout, LAN VLAN,
and attached downstream branches while changing the QNAP pair strengthens
the case for a problem that depends on the switch ports or their LACP state.
It makes a general Firewalla LAN-bond configuration problem less likely.

It does not isolate a defective physical port conclusively. QNAP's advertised
operational key changed **3 → 1**, despite retaining configured group 4, and
the cable move restarted member negotiation. Physical port selection, the
port-dependent advertised key, and reset negotiation state were not independent
variables. A Firewalla driver interaction with this particular peer is still
possible.

This measures LACP negotiation plus ordinary reachability. It does not prove
aggregate throughput above one member's capacity or independently demonstrate
data forwarding on each member. Longer normal operation, a later reboot on
this pair, the opposite bring-up order, or the same Firewalla on another switch
remain useful controls. Leave the working pair in place for normal observation
before scheduling another disruptive test.

## Current configuration and restoration

- Only LACP group **4 on ports 1+2**, Long; Firewalla remains Slow.
- VLAN 10 untagged/PVID 10: **1, 2, 3, 4, 5, 6, 7, 10**.
- VLAN 1: port **8**. VLAN 3999: port **9** only. No tagged members.
- Ports 5, 6, and 10 retain their downstream connections.
- All mirror sources remain disabled; the inactive destination selector remains 7.

The [dated restore configuration](../examples/experiments/lan-lag-ports-3-4-long-20260906.yaml)
returns only group 4 to 3+4 with Long timeout and puts standalone 1+2 back on
VLAN 3999. It matched the pre-move baseline with an empty plan before applying
the target. Restoration must be coordinated with moving the cables back; it
is not armed or scheduled. The older undated/Short experiment files describe
different baselines and should not be used to restore this run.

Private snapshots, the configuration backup, packet captures, the passive
recorder manifest, and local execution logs are under
`backups/lan-ports-1-2-20260906T135419Z/` on the observer. Raw captures and
credentials are excluded from Git.
