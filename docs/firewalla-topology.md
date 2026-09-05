# Firewalla Gold Plus double-LACP topology

The intended path is:

```text
ONT --10G-- QSW-L2110 ==2x2.5G LACP== Firewalla WAN
OFFICE --10G-- QSW-L2110 ==2x2.5G LACP== Firewalla LAN
```

Both paths use one QSW-L2110, but they must remain different Layer-2 networks.
The switch does no routing between them; all WAN/LAN traffic must cross Firewalla.

## Reference port and VLAN plan

| Interface | Connection | Membership |
|---|---|---|
| Port 9 | ONT | WAN-transit VLAN 3999, untagged only |
| LAG 1, ports 1+2 | Firewalla WAN | VLAN 3999, untagged only |
| LAG 2, ports 3+4 | Firewalla LAN | native LAN untagged; internal VLANs tagged |
| Port 10 | office switch | same LAN trunk as LAG 2 |
| Port 5 | LAN access/test host | native LAN only |
| Port 8 | rescue laptop | VLAN 1, untagged, until QSS management behavior is proven |

Never add ports 3, 4, 5, or 10 to VLAN 3999. Remove ports 1, 2, and 9 from VLAN 1.

Deployment status 2026-09-05: Firewalla's flat LAN is now `bond0` over
`eth3`+`eth2` after reverting the router-port test. WAN is now `bond1` over
`eth0`+`eth1`, and both WAN members synchronize and collect/distribute.
`eth0` connects to QNAP port 2 and `eth1` to port 1, both at 2.5G. The ONT
connects to port 9 at 1G. The switch uses native LAN VLAN 10 and isolated
WAN-transit VLAN 3999 per `examples/firewalla-gold-plus-flat-lan.yaml`.
The office link on port 10 currently negotiates 2.5 Gb/s. LAN still forwards
through only `eth3` / QNAP port 3; `eth2` / QNAP port 4 remains defaulted.

During the WAN switchover, one WAN member briefly had no physical link;
the user reported a possibly loose cable. After reseating, both WAN members
show actor/partner states 61/63, and all 20 probes passed for five minutes.
The successful WAN group demonstrates two-member LACP interoperability
between these same devices, narrowing the unresolved problem to the LAN
setup. See the [diagnostic report](lacp-diagnostics-20260905.md).

The flat-LAN configuration is restored to the original **ports 3+4** in
LAN LAG 2; port 7 is again an ordinary LAN access port. The restore was backed
up, applied, saved, and read back successfully. Both LAN cables are back on
ports 3+4. Reboot persistence of the restored switch configuration has not
been retested.

LAN LACP diagnosis on 2026-09-05: port 4 failed negotiation after a cable
replacement, swapping the two Firewalla connections, and a switch power cycle.
Either Firewalla member forwards through port 3. Temporarily substituting
port 7 for port 4 reproduced the same failure: the second member links at
2.5 Gb/s but the switch advertises a defaulted LACP state and zero partner
identity. The applied settings matched the YAML and no loop violations were
reported. This weakens the hypothesis of an isolated port-4 defect; the
broader negotiation failure remains unresolved. After the cable swap, port 3
connects to Firewalla `eth2`, and the second member is `eth3`. A subsequent
Firewalla reboot did not clear the fault: `eth2` remains collecting/distributing
while `eth3` reports a defaulted switch partner and does not forward. Firewalla
services and all three observatory collectors recovered. The observatory's
Google ICMP probe began timing out after reboot despite successful direct
pings to 8.8.8.8 from both the observer and Firewalla; the other monitored
targets pass. The cause of this probe discrepancy is unconfirmed.

The subsequent Firewalla LAN-member change from `eth2`+`eth3` to
`eth3`+`eth1` also reproduced the fault. Partner-port details map `eth1` to
QNAP port 3 (active, actor/partner states 61/63) and `eth3` to QNAP port 4
(backup, states 13/71). Both physical links are 2.5 Gb/s full duplex;
`eth2` is disconnected. QNAP's LAG table labels port 3 up and port 4 down,
with no loop violations; the later UI audit found that this status column
does not prove individual-member forwarding. Firewalla's protocol state
verifies that the replacement router port
works, but does not establish a defective router NIC: earlier swaps showed
`eth3` forwarding through QNAP port 3. All 20 observatory probes, including
Google ICMP, and all three Firewalla collectors pass at this latest check.

See the [QNAP UI audit](qnap-ui-audit-20260905.md) for screenshots, native
form comparisons, hidden-page limitations, and remaining switch-side tests.

Before the ingress-mirror test, the user restored the router's original LAN
pair and swapped its cable mapping: `eth3` now identifies QNAP port 3 and
collects/distributes (states 61/63), while `eth2` identifies QNAP port 4 and
remains defaulted (13/71). Both links are 2.5G. All 20 probes pass. The
capture laptop was directly connected to port 7 at 1G; that port remains
outside the LAG and is now disconnected. These observations supersede the
earlier interface mapping.

QSS management at 192.168.1.72 is reachable through the LAN with rescue port 8
disconnected. This confirms LAN management access; WAN-side management
isolation remains untested.

## Firewalla constraints

Firewalla Gold Plus supports the necessary two dynamic 802.3ad groups, one WAN
and one LAN. LAN LAG can carry Firewalla VLAN networks; WAN LAG cannot itself be
a VLAN interface. A single flow is limited to one 2.5G member. Firewalla's own
multi-flow testing reached roughly 4.7 Gb/s with four parallel flows and QoS off.

References:

- [Firewalla LAG guide](https://help.firewalla.com/hc/en-us/articles/4409583011091-Link-Aggregation-Groups-LAG)
- [Firewalla Gold Plus LAG benchmark](https://help.firewalla.com/hc/en-us/community/posts/8657015925011-Firewalla-Gold-Plus-4x2-5Gbit-Updates)
- [QNAP QSW-L2110-10T](https://www.qnap.com/en-us/product/qsw-l2110-10t)

## Decision points

- Untagged DHCP/static ISP handoff is the cleanest case.
- PPPoE may hide Layer-3/4 flow information and distribute poorly.
- An ISP-tagged handoff might be presented untagged to Firewalla by the switch,
  but this is a workaround requiring isolation and packet-level validation.
- If QNAP-to-Firewalla WAN traffic stays on one member during multi-flow tests,
  abandon the design or accept a 2.5G downstream ceiling.
- On Firewalla Gold Pro, use direct single 10G WAN and LAN links instead.

## Failure posture

This is a fail-open shared-switch design. A reset or VLAN mistake can put the ONT
and LAN into one broadcast domain. LACP does not mitigate switch, power, firmware,
or configuration failure. Keep the ONT disconnected during every reset, restore,
or configuration experiment, and run the negative isolation tests after every
firmware or configuration change.
