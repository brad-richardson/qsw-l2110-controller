# QSW-L2110 / Firewalla: only one LAN LACP member forwards

Draft support report, 2026-09-05. Not submitted. Root cause and vendor
responsibility remain unconfirmed.

## Reproduction configuration before the direct-laptop test

- QNAP QSW-L2110-10T, QSS 2.2.3.20260713, hardware A0.
- Firewalla Gold Plus, Linux 5.15.0-27-generic; both tested NICs use `igc`,
  reporting NIC firmware `2013:8877`. Firewalla application/box release
  identifiers have not been collected for this report.
- Firewalla LAN `bond0`: dynamic 802.3ad, members `eth3` and `eth2`,
  `layer2+3` transmit policy, active LACP, slow requested partner rate,
  `min_links=0`, stable aggregator selection.
- QNAP LAN LAG 2: ports 3+4, dynamic LACP, short timeout, priority 128.
  Both ports have the same untagged VLAN 10 and PVID 10.
- `eth3` connects to QNAP port 3; `eth2` connects to QNAP port 4 after
  the user's final restoration and cable swap before the ingress capture.
  Both physical links are 2500 Mb/s full duplex.
- WAN remains a direct ONT-to-`eth0` connection. QNAP WAN LAG 1, ports 1+2,
  and ONT port 9 remain physically disconnected. Their isolated switch VLAN
  is 3999. Firewalla `eth1` is currently disconnected.

Expected: both LAN members synchronize and collect/distribute frames.

Observed: `eth3` is active, actor/partner states 61/63; `eth2` is backup,
states 13/71. QNAP's LAG page labels port 3 up and port 4 down, but the
later UI audit found this column's per-member semantics unverified; it
does not establish physical carrier or collecting/distributing state.
The bond's reported aggregator size of two is insufficient to establish
that both members forward.

## Additional evidence collected at 03:04 UTC

Simultaneous 40-second passive captures used `tcpdump -p -nn -e -vvv -s 256`
on both physical members, filtered to EtherType 0x8809. Promiscuous mode was
not enabled. The filter includes LACP and vendor OAM, not ordinary IP traffic.
Capture timestamps printed by Firewalla are four hours behind the UTC
directory timestamp. Both captures reported zero kernel capture drops.

| Link | Firewalla-origin LACPDUs | QNAP-origin LACPDUs | Observed behavior |
|---|---:|---:|---|
| `eth1` / QNAP 3 | 38 | 2 | Both sides advertise synchronization, collecting and distributing, and identify each other |
| `eth3` / QNAP 4 | 69 | 40 | Firewalla identifies QNAP port 4; QNAP advertises Default with all-zero partner identity throughout |

The failing member's outgoing LACPDUs have the same Firewalla actor system
and key as the working member, with a distinct actor port number. Both
links advertise the same QNAP actor system and key, with distinct QNAP ports.
This is evidence of asymmetric partner learning, not proof that QNAP
receives those outgoing packets: sender-side capture occurs before physical
transmission. A switch ingress mirror or Ethernet tap can add evidence about
packet arrival; the mirror's treatment of nonforwarding members must also be
established. The later ingress test below illustrates this limitation.

EEE is disabled on both Firewalla members, and QNAP reports EEE inactive on
both links. Firewalla NIC CRC, missed, RX/TX error, and TX timeout counters
are zero. QNAP reports no loop violations, no RX bad packets, and one
historical TX bad packet on port 3 that did not increase during this check.

Private raw evidence is in
`backups/lacp-diagnostics-20260905T030426Z/`:

- `lacp-bidirectional-eth1.txt`
- `lacp-bidirectional-eth3.txt`
- `qnap-state.json`

## Experiments already performed

| Experiment | Result |
|---|---|
| Replug and replace the failing member's cable | Same failure |
| Swap router connections between QNAP ports 3 and 4 | Forwarding followed QNAP port 3; both original router NICs worked there |
| Cold power-cycle QNAP | Same failure; saved configuration persisted |
| Replace QNAP member port 4 with port 7 | Same failure on the second member |
| Restore QNAP members 3+4 and reboot Firewalla | Same failure |
| Change Firewalla LAN members from `eth2`+`eth3` to `eth3`+`eth1` | New `eth1` works through QNAP 3; `eth3` through QNAP 4 still fails |
| Disconnect working `eth1` / QNAP 3 for approximately 51 seconds | Observer lost LAN gateway connectivity; it recovered after `eth1` reconnected |
| Restore LAN `eth3`+`eth2` and map `eth3` to QNAP 3, `eth2` to QNAP 4 | Same one-member failure; all 20 network probes pass after reconnection |
| Connect failing Firewalla `eth2` directly to a macOS laptop's 1G adapter | Laptop received one LACP frame, proving transmission to an independent receiver at 1G |

These experiments weaken an isolated cable or single physical-port defect.
They do not establish a specific firmware defect. Whether the user fully
deleted/recreated the Firewalla LAG, rather than edited its membership,
has not been confirmed.

For the single-member test, Firewalla kernel events record `eth1` down at
03:10:33.525 UTC and up at 03:11:24.966 UTC. The observatory's LAN gateway
probe was failing around 03:10:35 through 03:11:25 and passing by 03:11:30.
Thus the remaining link did not restore observed connectivity within that
approximately 51-second interval. The planned 90-second window was not
reached. A two-second bond-state recorder started at 03:11:33, after
reconnection, so it did not capture the isolated member's negotiation
states during the outage. Its output is under
`backups/lacp-single-member-20260905T031131Z/`.

Read-only inspection of Firewalla's installed
`/home/pi/firerouter/plugins/interface/bond_intf_plugin.js` confirms that
FireRouter loads Linux's bonding module and configures bond creation and
member attachment. This identifies software layers Firewalla engineers
could adjust; it does not identify a specific effective workaround.

The subsequent [QNAP UI audit](qnap-ui-audit-20260905.md) matched all active
LAG settings, found no configured ACL or rate-limiting explanation, and
identified a controller/native-UI serialization difference for disabled
ports. A vendor-UI-only reapply or recreation remains an important
unperformed control before treating a firmware defect as established.

## Ingress mirror with a directly connected laptop

On 2026-09-05, a macOS laptop's 1G USB adapter was connected directly to
QNAP port 7. The complete MAC table confirmed that adapter on port 7,
outside the LAG. The laptop captured LACP in promiscuous mode throughout
both phases. A simultaneous 85-second Firewalla capture covered most of
the failing-port phase and all of the working-port control.

| Source mirrored to port 7 | Read-back confirmed enabled (UTC) | Disabled confirmed (UTC) | Laptop LACPDUs |
|---|---|---|---:|
| QNAP 4 ingress, requested 45 seconds | 03:59:35.566 | 04:00:22.948 | 0 |
| QNAP 3 ingress, requested 30 seconds | 04:00:31.030 | 04:01:03.413 | 31 |

Enabling takes effect before read-back completes, and disabling takes time;
the table records confirmation times, not exact hardware boundaries. All 31
laptop frames identify Firewalla actor port 1 and QNAP partner port 3, with
states 61/63. They span 04:00:29.761–04:01:01.263 UTC. No actor-port-2 /
QNAP-partner-port-4 frame appears anywhere in the laptop capture.

Across the simultaneous Firewalla capture, `eth2` recorded 141 outgoing
Firewalla LACPDUs and 84 incoming QNAP port-4 LACPDUs. QNAP still advertised
Default with zero partner identity. Working `eth3` recorded 81 outgoing and
three incoming LACPDUs, with both partners learned. These are whole-capture
counts, not counts restricted to the mirror windows. The decoder was
cross-checked against tcpdump's verbose output.

This proves the chosen mirror path can expose LACP on the working member.
It does not prove that port 4 physically receives no LACP: mirroring may
depend on per-member forwarding state or occur after an ingress filter.
A later pair of counter reads, separated by a 12-second pause plus request
time, showed port 4 receiving 23 additional good packets and zero bad packets.
Their protocol is unknown. Thus a completely dead receive path is not
supported by the available evidence, and vendor responsibility remains open.

Both mirror phases used backups and read-back verification. All ingress and
egress mirror sources are disabled afterward; the inactive destination
selector remains 7, consistent with the native UI's all-sources-unchecked
behavior. No configuration save was requested. All 20 probes pass afterward.

Private evidence:

- `backups/lacp-ingress-comparison-20260905T035935Z/`: laptop PCAP and decoded summaries.
- `backups/lacp-during-ingress-20260905T035946Z/`: simultaneous router PCAPs and capture diagnostics.
- `backups/qnap-ingress-20260905T035927Z-port4/` and `-port3/`: backups and mirror read-backs.

The reusable [diagnostic tools](diagnostic-tools.md) implement capture,
temporary mirroring with cleanup, and LACP state summaries.

## Direct Firewalla-to-laptop capture at 04:20 UTC

The user connected the laptop directly to Firewalla's physical port 2 and
provided a short capture. It contains one 124-byte Ethernet LACPv1 frame at
04:20:34.825076 UTC. Its Ethernet source matches the recorded permanent
address of `eth2`, confirming the interface independently of the case label.
The actor identifies the existing bond system, actor port 2, key 9, and
state 77. The partner system is zero because the laptop does not participate
in LACP. Both the repository decoder and tcpdump decode the same fields.

This proves that Firewalla `eth2` can physically transmit LACP to an
independent receiver. The laptop adapter is limited to 1G, so the experiment
does not reproduce the failing 2.5G QNAP link or exclude speed-dependent
behavior. The actor-key change from 11 to 9 is consistent with the
[Linux 5.15 bonding implementation](https://github.com/torvalds/linux/blob/v5.15/drivers/net/bonding/bond_3ad.c):
the key includes link speed and duplex. It is not evidence of configuration
drift between the two captures. One packet is sufficient to demonstrate
transmission, but does not establish sustained reliability or loss rate.

After the capture, live state showed `eth2` disconnected and `eth3` still
collecting/distributing through QNAP port 3 at 2.5G. All 20 probes passed.
No switch or router configuration was changed by the assistant for this test.

Private evidence is in `backups/lacp-direct-laptop-20260905T042034Z/`:
`laptop.pcap` and `summary.json`. The user-provided original
`firewalla-ingress.pcap` remains ignored in the checkout.

## Remaining discriminating tests

1. If a longer single-member test is needed, start recording before
   unplugging working port 3, leave port 4 connected for about 90 seconds,
   then reconnect port 3. The shorter test above has already shown loss
   of LAN connectivity; repeating it is deferred.
2. Test the separate WAN LAG using the remaining Firewalla interfaces
   `eth0`+`eth1` and QNAP ports 1+2 after the latest LAN-port restoration. LACP negotiation can be checked before
   attaching the ONT to QNAP port 9; doing so interrupts internet service.
   The current LAN members `eth2`+`eth3` must not be reused. Rollback means
   restoring WAN to `eth0` alone and reconnecting the ONT directly to it.
   User reports Bluetooth recovery is available. This tests a different
   group and port pair, but still uses the same two implementations.
3. Ask QNAP to explain mirroring and control-frame handling on an unsynchronized
   LAG member. The ingress test above found packets only on the working-port
   control. A tap or a validated pre-filter capture point would better establish
   whether Firewalla's failing-member LACPDUs reach the switch.
4. If needed, compare against another LACP-capable switch or another
   two-NIC host to isolate the implementation responsible.

QNAP's [installed firmware release notes](https://www.qnap.com/en/release-notes/qss/2.2.3/20260713)
do not describe an LACP fix or known issue. The
[Linux bonding documentation](https://docs.kernel.org/networking/bonding.html)
describes independent LACP rate requests; a slow/fast setting difference
alone does not demonstrate a configuration mismatch.
