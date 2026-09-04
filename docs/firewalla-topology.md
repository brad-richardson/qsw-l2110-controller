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

Deployed 2026-09-04: the Firewalla LAN is one flat bridge (`br0` over eth1-3, no
VLAN networks, WAN `eth0` on DHCP with DHCPv6-PD), so the LAN side uses a single
native VLAN 10 per `examples/firewalla-gold-plus-flat-lan.yaml`. Firewalla's LAN
and WAN LAGs are not yet configured; `/proc/net/bonding` is empty.

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
