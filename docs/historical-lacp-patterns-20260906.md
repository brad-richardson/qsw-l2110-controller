# Historical LACP patterns — independent review, 2026-09-06

## Strongest findings

1. **A previously successful 2+3 pair cannot be a prerequisite for the successful 1+7 sweep trial.** The first valid USB 1+7 success was trial 6 at 18:40:37 UTC. The first 2+3 trial was trial 13 at 18:52:30. The immediate predecessor of 1+7 was failed 1+6; before that came failed 1+5 and 1+4. A weaker hypothesis that the second NIC/actor-port previously negotiating through *either port 2 or 3* primes later behavior remains untested.
2. **The one 1+7 success is real initial negotiation, but not demonstrated stability.** Both native members had state 61/61, matching key 1 and QNAP ports 1/7, in one aggregator. Fresh reciprocal clean packets occur in the actual trial window (parent independently rechecked the raw captures). Native clean state persisted until the cable move around 95.6 seconds. That exceeds a simple stale-native screenshot but remains shorter than the earlier 220–237-second delayed failures on 3+4. The current repeat failing from startup differs from that precise delayed-failure pattern.
3. **The repeat changes more than the random actor MAC.** Sweep 1+7 advertised actor `02:17:fa:21:54:71`; repeat uses `02:3f:6c:3a:ff:a7`. Additionally, the old peer was rebuilt **0.092 seconds after configuration verification**, whereas the repeat peer was rebuilt **116.204 seconds after configuration verification**. A recent membership transition, hardware programming timer, partner aging, and physical-link history are thus confounded with actor identity.
4. **A stable binary port mask is inadequate.** All pairings among 1/2/3 passed the sweep; 1+7 passed once, but 2+7 and 3+7 failed, and the later 1+7 repeat failed with matching exposed config. Ports 4/5/6/8 failed with every partner. This favors port-dependent state or handling, but does not establish why 7 temporarily differs.
5. **The earlier reboot/mirror evidence is the strongest historical sign of switch-side ingress/state behavior.** Port 4 initially exchanged good PDUs, then its LACP stopped appearing at the switch mirror even though Firewalla kept capturing outgoing PDUs. QNAP defaulted about 93 seconds after the last mirrored PDU. Sender capture cannot establish wire delivery, but this predates the protocol failure and substantially narrows the issue beyond a cosmetic QSS status label.

## Chronology and controls

| Time / experiment | Settings and identity controls | Observation / relevance |
| --- | --- | --- |
| Sep 5 original 3+4, substituted 3+7 | Firewalla 2.5 Gb/s; LAN group initially 2; NIC/cable substitutions | Port 3 works on either Firewalla member; 4 and substituted 7 default with zero partner. Original notes lack the modern exact timing/evaluator quality. |
| Sep 5 16:13 group4 moved from3+4 to4+7, 7 unplugged | Firewalla; existing failed state; port4 advertised key changes3→4 | Port4 never learns partner over6.5min. Merely making4 lowest is insufficient. Separate physical4+7 outage evidence is incomplete and should not be conflated with this dummy-port case. |
| Sep5 17:17 port4 alone, then3 joins | Short;87s alone | Port4 continues defaulted despite144 outgoing Firewalla frames;3 joins and syncs. Reversing bring-up alone did not fix this existing3+4 state. |
| Sep5 dummy group5 on5+7, port4 linkreset; reboot | Short; added adjacent member; later loop prevention disabled and another reboot | Neither dummy configuration nor loop toggle gives sustained recovery. Raw reboot captures subsequently reveal ~10–12s real port4 sync, superseding the original blanket “never syncs” notes. |
| Sep5 Mac1+2 both connection orders | Group1 Short;1Gb; actor68:5e:dd:14:2e:92, memberports32/33 | Both orders exhibit recurring expiration; not the same missing-partner failure as Linux/Firewalla. |
| Sep5 Mac1+2 Short→Long→Short | Same peer/mapping; Long observation449s | Long sustains clean negotiation; Short reproduces expiration. Mac timing differences are a separate demonstrated timer interaction. |
| Sep6 firmware comparisons and12:17 single-LAG reboot | 3+4 group4 Long; Firewalla Slow2.5Gb; 2.2.1/2.2.2/2.2.3 examined | Delayed port4 failure~220–237s; unused secondLAG is not necessary. Final mirror captures loss before defaulting. |
| Sep6 14:04 production3+4→1+2 | Same Firewalla NICs/cables/identity, group4Long, Slow2.5Gb; no reboot | 1+2 jointly clean644.570s. Lowest operational QNAPkey changes3→1, so key and ports were not independently varied. |
| Sep6 15:57 1+2→1+4→1+2 | Same Firewalla identity/member order/key1, same VLAN; no reboot | 1+4 never syncs~103s; returning same member/cable to2 recovers. Successful good-port experience does not universally prime another port. |
| Sep6 16:12 1+2→1+8→1+3 | Same Firewalla identity,group4Long,Slow2.5Gb; VLAN rescue staging for8/3; no reboot | 1+8 never syncs~103s;1+3 jointly clean678.574s with throughput and clean counters. |
| Sep6 17:42/18:00 USB controls | ASIX CDC-NCM unknown native speed/duplex, then ax88179 driver no host carrier | **Invalid port comparisons.** Neither proves switch pair1+2/1+3 failure. |
| Sep6 18:12 two-r8152 1+3 control | Group4Long,Slow1Gb; actor02:b3:87:5c:0e:07; Realtek5c... actorport1→1, Anker a0... actorport2→3 |90s control; native clean from2.65s, currentjoint59.02s, validates working peer. |
| Sep6 18:34–19:19 sweep | Group4Long,Slow1Gb,VLAN1, actor02:17:fa:21:54:71 throughout; member insertion order fixed |1+2,1+3 succeed;1+4/5/6 fail;1+7 succeeds;1+8,2+8/7/6/5/4 fail;2+3 succeeds;allremaining fail. New Linux bond created every trial, so previous Linux bond's partner records are not retained. Driver and switch internal state may persist. |
| Sep6 19:01 resume | Same actor/NICorder retained; new namespace and run; no switch reboot |3+7 first recorded trial after resume fails; unrecorded interrupted original move must not be counted as another completed3+7 trial. |
| Sep6 19:26 repeat1+7 | Same exposed config/NICorder/speed/portmap, new actor02:3f:6c:3a:ff:a7; config-to-start delay116s |Completed 360.030s with zero clean native samples out of 430; port 1 clean, port 7=13/69, no carrier failures. Host cleanup passed. See the repeat report. |

## Best discriminating tests after the completed repeat

Do not repeat another entire sweep yet. Preserve explicit actor identity, mapping, timers, VLAN, and capture at every step. Test one variable at a time and record configuration-write completion, host start, and physical carrier events.

1. **Reproduce sweep-style startup on 1+7:** same current actor, temporarily remove/re-add the isolated bench LAG, then immediately rebuild the peer using the same path as the sweep; hold ≥360s. This separates the fresh-transition/116s-start-delay confound from a mandatory previous good pair. It changes configuration history deliberately, so is not an identity-only trial.
2. **Test priming with actor and startup procedure held constant:** 1+7 fail/control → 1+3 confirmed healthy → 1+7 for360s. Keep original Realtek on1 and move only Anker7→3→7; retain actorport1/2, bond actor MAC and switchgroup4. If7 recovers, repeat an unprimed/control cycle to distinguish repeatable priming from a one-off transition. The shortest faithful replay of the prior success is1+2→1+3→1+4→1+5→1+6→1+7; use only if simple good→7 comparison is inconclusive.
3. **Identity test with cables and QNAP config fixed:** old sweep actor versus current actor, with equal reset/down intervals and identical host rebuild steps, ideally A→B→A. Oldactor success alone would make identity/state lookup worth pursuing, but newactor-only failure without matched timing would remain ambiguous. Current CLI has no explicit actor flag; do not fake a resume of completed results as an experiment.
4. **Fresh isolated-switch boot on1+7, then failing reference1+4/3+4:** preserve bench-only context and hold≥360s. Distinguishes startup-then-decay from persistent runtime failure. No need to alter Firewalla. A reboot is a separately coordinated experiment, not implied by read-only investigation.
5. Only after repeatable stable success, test simultaneous disjoint groups1+7 and2+3. The present evidence does not justify treating that set as reliable.

## Evidence pointers

- `docs/linux-usb-sweep-results-20260906.md`, especially physical-order table and extended-hold limits.
- `docs/evidence/linux-usb-sweep-20260906.json` and `docs/evidence/linux-usb-realtek-control-20260906.json`.
- Local `backups/usb-sweep-20260906T183251Z/{summary.json,events.jsonl}`: trial0061+7, trial0132+3; successful configverified18:40:37.068595 and peerrebuilt18:40:37.160117.
- Local `backups/usb-1-7-repeat-prep-20260906T192356Z/events.jsonl`: configverified19:24:24.692118.
- Local `backups/usb-sweep-20260906T192611Z/events.jsonl`: existing-controlread19:26:19.791107, peerrebuilt19:26:20.896542.
- `tools/lacp_sweep.py:567` random actor creation;`:688` deletes/recreates bond each trial;`:965` immediate rebuild after switchapply;`:1114` resume preservesactor;`:1141` existing-controlpath skips switchwrite.
- `docs/firewalla-topology.md:48` actual older physical3+7 substitution.
- Local `backups/lan-order-20260905/summary.md` chronology and later corrections; do not adopt its superseded blanket conclusions.
- `docs/mirror-reboot-results-20260906.md`, `docs/firmware-comparison-results-20260905.md`, `docs/port-isolation-results-20260905.md`.
- `docs/lan-ports-1-{2,3,4,8}-results-20260906.md`.
- `docs/macbook-lacp-results-20260905.md` and `docs/macbook-timeout-results-20260905.md`.

This review used local evidence only. The [six-minute repeat report](linux-usb-1-7-repeat-20260906.md) contains its completed evidence and final device state. Public-code findings are documented separately. The historical review made no device changes.
