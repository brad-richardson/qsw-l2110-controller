# LACP experiment review and tests before removing the switch

Initial review at approximately 20:00 UTC on 2026-09-05, including the latest
Claude session, private experiment recordings, and fresh read-only
switch/Firewalla checks. Follow-up below records the subsequently authorized
Fast LACP tests and recovery after the user recreated the Firewalla LAG. The
two new spare-port YAML files are proposals, not completed experiments.

**Latest outcome:** Fast LACP did not recover port 4. Cycling the Firewalla
bond to apply it coincided with loss of internet reachability, and the
automatic return to Slow did not restore that reachability during recording.
After the user's LAG reset, gateway and internet probes passed again. The
user explicitly requested leaving that reset untouched; no further bond
changes are planned. No experiment timers or jobs remained pending at the
post-reset check.

The switch is the leading suspect, but the precise receive-path mechanism is
unconfirmed. A laptop can test the idle ports while the production LAN remains
connected. Moving the entire LAN off the switch is not necessary for that test.

| Experiment or observation | What it establishes |
|---|---|
| Cable swaps, router NIC swaps, alternative second member on switch port 7 | Weakens an isolated cable, NIC, or port-4 hardware defect; does not rule out all PHY or speed-dependent behavior. |
| Native UI group/timeout changes; later Firewalla bond recreation during recovery | Neither those switch settings nor the router recreation restored sustained two-member LAN operation. |
| Port 4 alone after both cables were removed, Short timeout, approximately 87 seconds | Simply plugging the higher member first did not recover this pair in its existing switch state. Does not establish that every pair or a cold-started switch is order-independent. |
| Dummy group on ports 5+7, failing-link reset, switch reboot | No sustained recovery; the proposed neighboring-port enable workaround failed. |
| Reboot with loop detection/prevention disabled and saved | Reproduced the transient success followed by failure. Repeating that toggle has little value. |
| WAN LAG on switch ports 1+2 | These devices have sustained two-member LACP at 2.5G. A universal implementation incompatibility is unlikely. |
| Raw ARP probe entering port 4, answered on port 3 | Ordinary ingress on port 4 reaches the switch. It does not establish where LACP is delivered internally. |

The main [diagnostic report](lacp-diagnostics-20260905.md) ends before several
of the evening experiments. Their detailed record is in the ignored
`backups/lan-order-20260905/summary.md`. Earlier statements that port 4
"never syncs" were superseded by the reboot captures.

Independent decoding of those PCAPs gives the following port-4 transitions:

| Reboot recording | QNAP first advertises state 63 | Firewalla first advertises state 61 | QNAP returns to state 71 with zero partner |
|---|---|---|---|
| Dummy group present, loop protection on | 18:14:23.187 | 18:14:25.327 | 18:14:35.435 |
| Loop protection off | 19:28:04.961 | 19:28:07.155 | 19:28:17.261 |

Both peers advertise synchronization, collecting, and distributing for about
ten seconds. This is strong evidence that initial negotiation can succeed;
it is not a measurement of application traffic on that member. The roughly
12-second delay from the first QNAP synchronized PDU does not locate the
instant ingress fails: it could include receive-state timeout behavior. No
capture shows when the firmware programs a trunk or which internal receive
machine receives a frame.

Likewise, the MAC table's use of port 3 is not by itself evidence of a bug.
The switch's bundled help explicitly identifies the lowest member as the
mapped bridge port; a reply can leave a different aggregate member from the
one on which its request arrived. Wrong LACP ingress attribution remains a
testable hypothesis, not an established root cause. The same evidence does
not completely exclude LAN traffic or a VLAN-specific interaction.

Fresh checks found Firewalla `eth2` connected to switch port 3 at 2.5G with
actor/partner states 61/63, and `eth3` connected to port 4 at 2.5G with states
13/71. LAN group 4 uses Short timeout; the Firewalla still requests Slow.
EEE is disabled, switch bad-packet counters are zero, and NIC CRC/error/timeout
counters show no obvious explanation. No ingress/egress traffic filters were
listed on either physical LAN member; the inspected bond filters match IP or
IPv6. This is not an exhaustive audit of Firewalla software.

The passive 40-second capture at 19:57:55–19:58:36 UTC recorded:

- Working `eth2`: 38 Firewalla PDUs, two QNAP PDUs, states 61/63 throughout.
- Failing `eth3`: 38 Firewalla PDUs, one QNAP PDU, states 13/71; QNAP's partner
  identity remained zero. Both captures reported zero kernel capture drops.

Sender-side capture still does not prove physical transmission of every PDU.
The fresh snapshot, PCAPs, decoded summaries, and independently decoded reboot
windows are private under `backups/lacp-review-20260905T195610Z/`.

The most useful next sequence uses the spare ports. Live reads confirmed
ports 1, 2, 7, 8, and 9 have no carrier; ports 5 and 6 are in use. WAN routing
uses Firewalla `eth0` directly. Physically confirm the ONT remains off the
switch and ports 1, 2, 7, and 9 are empty before repurposing this former WAN
group. Port 8 can retain its rescue configuration throughout.

1. **Laptop on the existing group 1, ports 1+2.** No switch configuration
   change is needed. This validates the laptop and adapters against the pair
   that worked for WAN. VLAN 3999 has no connected upstream or DHCP server;
   an IP address is unnecessary for LACP. Require sustained negotiation on
   both members for five minutes, not merely an initial synchronized frame.
2. **Laptop on group 1, ports 1+7.** Use
   [laptop-lag-ports-1-7.yaml](../examples/experiments/laptop-lag-ports-1-7.yaml).
   This brings a previously failing candidate port into a group whose lowest
   member is port 1. Only spare LAG ports 1, 2, and 7 are managed; port 7 moves
   from VLAN 10 to VLAN 3999. The production group on ports 3+4 is preserved.
3. **Laptop on group 1, ports 2+7.** Use
   [laptop-lag-ports-2-7.yaml](../examples/experiments/laptop-lag-ports-2-7.yaml).
   Keep the same adapter and cable on port 7. This holds peer, group number,
   VLAN, timeout, and the suspect port constant while changing the lowest
   member from 1 to 2. Compare both bring-up orders if results differ.

If port 7 works with port 1 but fails with port 2, the group/member mapping
hypothesis gains substantial support. If port 7 works in both pairs, a blanket
"port 7 cannot do LACP" claim is false at the laptop's tested speed. If it fails
in both while 1+2 works, the port/path remains suspect; the exact mechanism is
still unresolved. A failed 1+2 baseline requires checking the laptop setup
before interpreting later failures as switch evidence.

Start a new recording for each pair and mark each cable event. Remove both
laptop cables before each configuration change, wait for old peer state to
expire, and reconnect only after read-back. Waiting alone is not proof that
the switch's internal hardware state has reset. Keep laptop bridging/Internet
Sharing off. QSS writes the whole LAG table, even when the intended changes
are confined to spare ports, so these are lower-impact experiments rather
than a guarantee against firmware-induced LAN interruption. Monitor the
working LAN member and gateway while applying.

For each apply, use the normal controller backup, plan, and read-back flow.
After the laptop cables are removed, restore with
[lan-lag-ports-3-4-short.yaml](../examples/experiments/lan-lag-ports-3-4-short.yaml).
That restore matches the production baseline observed during this review;
recheck it against live state before use. These proposals were validated by
the controller's planner against the saved live snapshot and simulated
intermediate states; they have not been applied to hardware.

If the priority is a working production LAG, the existing
[LAN-on-1+2 runbook](runbook-lan-lag-ports-1-2.md) remains the best next candidate.
It requires a brief LAN interruption and two cable moves, rather than complete
switch removal. Keep the Firewalla bond intact during that move. Its result
also tests VLAN 10 and the actual 2.5G router peer, which the isolated laptop
test does not reproduce.

The review identified one lower-confidence experiment, subsequently tested
below: temporarily request Fast LACP on Firewalla as well as using QNAP Short.
Changing the switch's
timeout did not change Firewalla's own rate request. Independent rate requests
are normal under [Linux bonding](https://docs.kernel.org/networking/bonding.html),
so this is a compatibility probe, not correction of a demonstrated mismatch.
The fresh capture already shows Firewalla transmitting about once per second;
changing its request primarily asks QNAP to transmit more frequently.

Follow-up at 20:18–20:20 UTC: the user authorized a temporary Fast-rate test.
A detached recorder was started and an independent five-minute restore timer
was armed. Writing `fast` to the running bond's sysfs rate returned `EBUSY`
(Device or resource busy); the rate remained `slow 0`. The upstream 5.15
[option definition](https://github.com/torvalds/linux/blob/v5.15/drivers/net/bonding/bond_options.c)
sets `BOND_OPTFLAG_IFDOWN`, so the live-change claim in the conversation was
incorrect. The helper's ability to update actor state while up does not
override that entry-point restriction.

This first attempt did not test Fast LACP. At its final check the bond remained up with
the original one-member forwarding state. The restore timer and recorder
were stopped, and the captures were fetched to the ignored
`backups/lacp-fast-20260905T201627Z/`. A further rate test requires taking
`bond0` down, changing the rate, and bringing it up again from a detached
router job with recovery arranged in advance. That introduces a LAN outage
during renegotiation; returning to Slow requires the same cycle. The later
experiment below showed that restoring the rate and administrative state
alone was insufficient to recover internet access.

Follow-up at 20:30–20:41 UTC: the user authorized a detached bond down/change/up
test. A five-minute Slow restore and separate bring-up watchdogs were armed
before applying Fast. Recorded events show:

| UTC | Result |
|---|---|
| 20:30:29.686 | Bond was back administratively up with `fast 1`. |
| 20:30:29.823 | First failed internet probe; gateway and switch probes still passed. |
| 20:35:30.101 | Scheduled restore completed with the bond up and `slow 0`. |
| 20:36:33.863 | Last recorded internet probe still failed. |
| 20:40:15 | Read-only check after the user's reset found a newly created bond, up on Slow; no experiment jobs or timers remained. |

All 210 recorded gateway and switch probes passed. Internet probes to
`1.1.1.1` passed before the Fast cycle and failed from that cycle through the
end of recording, including after the scheduled Slow restore. Subsequent
post-reset probes passed 3/3 to the gateway and 3/3 to `1.1.1.1`. These checks
establish restored IP reachability, not an exhaustive application test.

During Fast, the working `eth2`/switch-port-3 pair advertised states 63/63
throughout its captured PDUs; QNAP sent 295 LACP PDUs in roughly five minutes.
The failing `eth3`/port-4 pair never advertised forwarding: QNAP sent only ten
PDUs, all state 71 with a zero partner identity. Firewalla's receive state
cycled through current, expired, and defaulted while port 4 continued its
roughly 30-second transmissions. Both captures reported zero kernel drops.
Fast therefore changed the working peer's cadence but did not fix the
failing member.

The reset recreated `bond0` with interface index 13, replacing index 6. It
remained on Slow, with `eth2`/port 3 forwarding and `eth3`/port 4 still failing.
The experiment helper was guarded against modifying any bond other than
index 6. The scheduled recovery jobs had already finished when checked;
attempts to stop their timers reported that the units were no longer loaded.
No settings on the recreated LAG were changed.

The recovery design restored the kernel rate and administrative state, but
did not restore full Firewalla forwarding. The recording does not include
routing and firewall state snapshots sufficient to identify why internet
access remained broken. Do not treat another live bond cycle as a minimally
invasive test or repeat Fast as an established workaround. The switch
configuration was unchanged throughout these Fast tests. Evidence is private
under `backups/lacp-fast-cycle-20260905T202657Z/`, including the rate event
log, packet summaries, and connectivity recording.

Before trusting the laptop harness, check these limitations:

- Confirm both adapters support native bonding, using the Mac's
  `networksetup -isBondSupported enX` for each actual interface. Apple's
  [networksetup manual](https://keith.github.io/xcode-man-pages/networksetup.8.html)
  documents that check. macOS supports LACP, but this review has not validated
  the user's particular USB adapters or the harness on their laptop.
- `tools/lacp_peer_test.py` caches the latest PDU from each Ethernet source.
  Neither `live_line` nor `member_summary` expires those entries or checks
  current carrier. An offline reproduction with old synchronized entries
  still reports `SYNCED` and `synced_at_end: true`. Use fresh PCAP timestamps
  and native bond/member status; restart recordings between runs. The code
  was not changed as part of this experiment review.
- The existing laptop runbook references
  `examples/experiments/laptop-lag-ports-7-8.yaml`, which is absent at commit
  `34e572e`. The spare-port alternatives above also preserve rescue port 8.
- A successful 1G laptop test does not prove Firewalla is faulty: peer, NIC
  driver, link speed, and Linux actor key all differ from the original test.
  Linux encodes speed in the actor key; see the
  [5.15 bonding source](https://github.com/torvalds/linux/blob/v5.15/drivers/net/bonding/bond_3ad.c).
  To investigate a remaining speed dependency, compare the same peer and
  ports at equal member speeds in a later controlled test.

Reserve factory reset and firmware comparison for an isolated session if
these tests do not produce a useful answer. First reproduce on current
firmware with a minimal native-UI configuration, then change firmware with
the same peer, speeds, and port pairs. The
[2.2.3 release notes](https://www.qnap.com/en/release-notes/qss/2.2.3/20260713)
mention a VLAN/MAC-learning fix but no LACP fix; that is a lead, not evidence
that the change caused this failure. Do not combine a reset and downgrade as
the first comparison, because a successful result would not identify which
change mattered.
