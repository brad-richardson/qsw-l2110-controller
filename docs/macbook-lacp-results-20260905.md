# MacBook LACP baseline — 2026-09-05

**Neither connection order met the required five minutes of sustained two-member LACP without expiration.** Both adapters negotiated 1000baseT full duplex and exchanged fresh LACPDUs with the expected switch ports. The Mac repeatedly entered expired state on each link, including while each was the only connected member. Reversing connection order did not remove that behavior. This does not identify the faulty implementation or establish a routed-traffic failure.

Only existing group 1 on ports 1+2 was tested. Firewalla’s recreated LAG, production cables, and switch configuration were untouched. No other pair or firmware version was tested.

Environment: macOS 26.6.2 (25G83), Python 3.14.6, Apple tcpdump 4.99.1/158. A new clean clone was updated with `git pull --ff-only origin main` at `7874d043577370d811d8aa818f27e66876f5079d`. Both requested handoff/review documents and installed native manuals were read; no local work was overwritten.

| Label / adapter | Interface / USB identity | Original MAC | Switch port | Native support / speed |
|---|---|---|---|---|
| A / Anker | en8 / Realtek 0bda:8153, AppleUserECM | a0:ce:c8:59:74:22 | 1 | YES / 1 Gbps full duplex |
| B / TP-Link | en9 / ASIX AX88179B 0b95:1790, AppleUSBNCMData | c8:a3:62:f4:21:b0 | 2 | YES / 1 Gbps full duplex |

Sequential cable connections, carrier observations, and captured switch port IDs established this fixed mapping.

“Clean” requires native actor/partner synchronization, collecting, and distributing on both members, neither expired nor defaulted, both carriers up, and fresh bidirectional packets. Native interval estimates use approximately one-second samples. Both baselines ended with SIGINT while both cables remained connected.

| Connection order | Both connected observation, UTC | Observed duration | Longest clean native interval | Kernel drops en8 / en9 | Result |
|---|---|---:|---:|---|---|
| Port 1, then port 2 | 21:32:58.789–21:38:14.817 | 316.0 s | 2.06 s | 0 / 0 | Fail |
| Port 2, then port 1 | 21:42:34.077–21:47:43.210 | 309.1 s | 2.05 s | 0 / 0 | Fail |

A verified 120.62-second carrier-down interval preceded the port-1-first repeat; a separate 120.50-second interval preceded port-2-first. These were aging intervals, not proof of a firmware reset. No reboot or switch reconfiguration occurred.

| Order | Member | LACP PDUs Mac / QNAP | Median interval seconds Mac / QNAP | Mac state 0xbd PDUs |
|---|---|---:|---:|---:|
| 1 → 2 | en8 | 362 / 93 | 1.001 / 4.000 | 89 |
| 1 → 2 | en9 | 317 / 81 | 1.001 / 4.000 | 78 |
| 2 → 1 | en8 | 310 / 79 | 1.001 / 4.000 | 77 |
| 2 → 1 | en9 | 349 / 89 | 1.001 / 4.000 | 86 |

The recurring Mac actor transition was `0x3d` → `0xbd`: synchronization/collecting/distributing bits stayed set, but the **expired** bit appeared. The Mac's stored partner state changed from `0x3f` to `0x37`, losing synchronization. Subsequent QNAP arrivals restored current state, and the cycle repeated. Fresh QNAP PDUs generally advertised actor `0x3f` after startup and echoed the Mac's expired advertisement in their partner fields. Mac sends were approximately one second apart; QNAP arrivals were approximately four seconds apart. This is an observed timing pattern, not an established root cause.

Mac actor system ID was `68:5e:dd:14:2e:92`, priority 32768, key 1, member ports 32/en8 and 33/en9. QNAP actor system ID was `24:5e:be:77:e5:86`, priority 32768, key 1, ports 1 and 2. Corresponding actor/partner system IDs, priorities, keys, and port IDs matched after startup; reported transient identity mismatches in the first-order repeat preceded the two-member observation window.

The harness's labels were unreliable here. It reported en8 `SYNCED` even with expired state, and classified TP-Link's own frames as switch frames because the actual en9 Ethernet source remained `c8:a3:62:f4:21:b0` while native ifconfig displayed the bond MAC `a0:ce:c8:59:74:22`. Independent decoding classified each PDU by its actor system ID and checked packet timestamps and native state. PCAPs were also decoded with tcpdump independently. Vendor-private OAM frames captured on en8 were retained but excluded from LACP counts.

Anker briefly lost carrier at 21:32:13.558–21:32:16.646 UTC, before the first-order two-member window. Neither verified observation window had a carrier drop. An earlier exploratory run also had a three-second drop; the user reported cable handling without intentional reseating. Cause remains unassigned.

Native setup required a local workaround: `networksetup -createBond` and explicit `-addDeviceToBond` calls left an empty `LacpUsbTest` configuration despite both support checks returning YES. `ifconfig bond0 bondmode lacp` and `bonddev en8/en9` successfully attached the members, verified by `ifconfig -b -v` and `networksetup -showBondStatus`. The temporary device was bond0, index 34. Bond IPv4/IPv6 were off; no LAN IP or gateway was assigned. The original member services remained DHCP/automatic IPv6 and acquired automatic link-local addresses while connected.

An exploratory first run inherited a blocked signal mask and could not save tcpdump’s final counters. Its PCAPs remain preserved, but it is excluded from the baselines above. Explicit signal unblocking was verified with a two-interface capture/interrupt check, then both orders were recorded with complete counters. Repository harness code was unchanged.

Wi-Fi remained the default route on en0 via 192.168.1.1. The two verified baselines recorded 76 Wi-Fi-sourced internet probes, 0 failures, and 0 default-route departures. Preflight, aging intervals, the exploratory run, and postflight also have saved connectivity evidence. Postflight ICMP and HTTPS checks passed.

**Cleanup completed after both Ethernet cables were unplugged.** Only the temporary bond was removed; both permanent adapter MACs, original adapter service settings, service order, absence of bonds, and Wi-Fi route were verified against preflight. Test capture, monitoring, and keep-awake processes were checked and stopped. The machine retains its original network services.

This isolated 1 Gbps test assesses negotiation, not throughput or routed traffic. It does not prove Firewalla is faulty, and it does not reproduce all conditions of the 2.5 Gbps production LAG. Zero reported kernel capture drops does not prove absence of all driver or physical-path loss.

A proposed next timer probe is a controlled Short versus Long timeout comparison on the spare group, holding the same Mac, adapters, cables, and ports 1+2 fixed. This has not been tested with this Mac and is not a promised fix; it requires a separate switch-change plan and read-back. The handoff also proposes group 1 on 1+7, then 2+7, but the failing 1+2 Mac baseline limits how those comparisons can be interpreted. The earlier Firewalla Fast-rate and switch loop-protection experiments did not resolve the production issue; do not repeat the disruptive Firewalla cycle as a routine next step. For a subsequent firmware comparison, first confirm a supported downgrade path for this exact model and preserve the current configuration and version. Hold other variables fixed in a separate maintenance window. Treat a factory reset as a separate variable. No firmware change was performed in this session. [QNAP's 2.2.3 release notes](https://www.qnap.com/en/release-notes/qss/2.2.3/20260713) and [Apple's native aggregation guidance](https://support.apple.com/guide/mac-help/combine-ethernet-ports-a-virtual-port-mac-mchlp2798/mac) were consulted; local native evidence determined the result.

Original PCAPs, tcpdump logs/decodes, native snapshots, event marks, summaries, connectivity checks, setup diagnostics, and cleanup evidence remain in ignored `backups/macbook-lacp-20260905T210804Z/`. Its `report.md` and `methodology.md` retain the longer session record.

The [durable compact evidence summary](evidence/macbook-lacp-20260905-summary.json) contains both verified runs’ UTC events, native interval measurements, decoded packet counts/states/identities, capture counters, connectivity results, and SHA-256 hashes of the original PCAPs. Raw captures and full native logs remain in the local ignored evidence directory.
