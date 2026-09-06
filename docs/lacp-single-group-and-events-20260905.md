# Single-LAG and device-event comparison — 2026-09-05

**Disabling the unused first LAG did not recover the production LAN pair.** On restored QSS 2.2.3.20260713, port 4 remained defaulted throughout a 451-second observation. The switch now has only production LACP group 4 enabled, on ports 3+4, still Long. Wi-Fi internet stayed reachable during this change and observation.

The user authorized disabling the first LAG and requested Firewalla flow/device recording to investigate whether a reconnect or traffic could trigger the failure. No Firewalla network configuration, device-block rules, or production cables changed. No other port pair was tested.

## Exact change and result

Before the write, the script downloaded a full snapshot and `.cfg`, checked firmware and the original two-LAG configuration, verified Wi-Fi management ingress on ordinary switch port 6, and started the LACP recorder. A separate passive event recorder was already running.

The only desired configuration changes were `portTypeId_1` and `portTypeId_2`, both from `"2"` (LACP) to `"0"` (disabled LAG membership). QSS requires the full LAG table. Readbacks before save, after save, after observation, and after watcher validation matched the target exactly. VLANs, PVIDs, port admin/speed/EEE/flow settings, and group 4 were preserved.

- Save/readback completed September 6 **00:46:29 UTC**.
- Observation: **00:46:29.496834–00:54:00.711717 UTC**, 451.215 seconds.
- Jointly clean negotiation: **0 seconds**, with 444 privileged native samples.
- Native `eth2` / switch port 3: actor/partner **61/61**. Native `eth3` / port 4: **13/69**.
- Fresh LACP packets in the measured window: 29 on `eth2`, 43 on `eth3`. Final capture counters: **zero kernel drops** on both.
- Final canonical configuration SHA-256: `095e9b619f7d91c3a3a998906f9d812e538c0342660f0a79c365b0895f433c9c`.

This tests removal of LAG 1 from an **already-failed runtime state**. It does not test booting with only one LAG. A failure that persists after its initiating condition disappears could survive this live configuration change.

## Traffic and reconnect evidence

For the earlier [three firmware transitions](firmware-comparison-results-20260905.md), we preserved Firewalla's existing archived `conn`, `conn_long`, and DHCP logs, FireMain/FireRouter logs, and the continuously recorded kernel audit and member interface counters. LACP and native timestamps come from Firewalla; its clock reported NTP synchronized. Zeek timestamps are epoch values, FireMain's displayed times were interpreted in America/New_York, and kernel timestamps include their UTC offset.

The initial historical extraction yielded 20,145 unique connection IDs and 69 DHCP exchanges. Connections were deduplicated across normal and long-flow records by UID, retaining the longest reported duration. These counts describe the collected records, not all packets or all connected devices.

| QNAP port-4 default, UTC | Flow starts within ±5 s | DHCP exchanges within ±15 s | FireMain discovery/online/offline lines within ±60 s | Nearest DHCP exchange |
|---|---:|---:|---:|---|
| 00:00:44.549364, QSS 2.2.2 | 106 | 0 | 0 | REQUEST/ACK 43.523 s afterward |
| 00:18:40.419069, QSS 2.2.1 | 24 | 0 | 0 | REQUEST/ACK 17.647 s afterward |
| 00:36:48.796286, restored 2.2.3 | 21 | 0 | 0 | REQUEST/ACK 20.714 s beforehand |

The nearest device-discovery messages occurred 233.549, 231.419, and 229.796 seconds before the failures, around recovery from reboot. There were no bond/member kernel link events within ±60 seconds of the failures. Traffic was present in every case, with different connection volumes; these records do not identify a common triggering device. Native RX/TX error counters did not increase in the inspected surrounding interval. One-second interface counters do not resolve brief microbursts or switch CPU pressure.

Before the single-LAG write, additional passive recording began on Firewalla:

- **74,184 conntrack events**, with event/receipt timestamps and connection metadata; the collector ended normally without a reported netlink overflow.
- **1,663 neighbor/link notifications**. Neighbor-state changes alone do not establish device reconnection.
- **3,651 ARP/DHCP packets** captured on `bond0`, with zero kernel capture drops.
- Tailed normal/long connection and DHCP logs, with a separate receipt timestamp on each line; 2,851 normal flow lines, 5,082 long-flow lines, and 25 DHCP lines.
- Before/after neighbor, link, bond, and conntrack-stat snapshots. Existing application logs were also retained and backfilled after observation.

The event recording ran from before the configuration write until after the observation ended. There was **no LACP recovery or new clean-to-failed transition** in that single-LAG window to correlate with a device event.

Log files rotate. The tail collectors reported temporary missing/reappearing Zeek files and followed replacements; archived logs were retrieved separately to preserve surrounding history. The FireMain base-file tail produced no lines, so its empty live stream is not evidence that the application logged no events in rotated files. Application-log conclusions above use the retrieved historical files.

Flow `ts` is the connection's reported start time, not necessarily when its record was written. A long connection can be logged later or repeatedly, and total flow bytes cannot be assigned to an arbitrary instant. The conntrack stream supplies contemporaneous metadata for the later experiment. DHCP REQUEST/ACK can be a lease renewal; a reconnect can occur without DHCP. Firewalla also cannot observe every exchange switched locally between endpoints. Consequently the absence of a nearby log event is useful negative evidence, not proof that no device reconnected or emitted a relevant frame.

## Five-second watcher for manual device isolation

Run this from the repository on the Mac:

```console
.venv/bin/python -m tools.lan_lacp_watch
```

The [watcher](../tools/lan_lacp_watch.py) uses the prepared private SSH plan and performs **no switch writes, firmware operations, Firewalla configuration changes, or device blocking**. It starts a bounded passive recorder, transfers only newly recorded bytes every five seconds, and displays current native member states plus a clean-negotiation timer. Type a note such as `TV powered off` and press Enter to save a timestamped marker.

`CLEAN NOW` requires current native state and fresh reciprocal member packets. `RECOVERY OBSERVED` requires the **current uninterrupted** clean interval to reach five minutes; a successful interval earlier in the recording cannot satisfy it. Final capture integrity remains pending until cleanup. Missing/stale evidence yields no recovery claim. Ctrl-C stops the owned recorder and audits capture drops; the default duration is one hour, configurable with `--seconds` up to two hours. Evidence goes into a new ignored `backups/lan-watch-<UTC>/` directory. Keep the terminal running and Mac awake during the test.

Protect the user's Mac, **all Apple devices**, bradflix, Firewalla, APs, switches, and uplinks. Leave unidentified devices alone. For an identified nonessential endpoint, power it off, disable its Wi-Fi, or disconnect only its own Ethernet cable, record the action, and wait for fresh negotiation evidence. Restore it before testing another endpoint if the goal is a controlled comparison. A repeatable recovery on removal followed by failure on restoration is stronger evidence than a single coincidence.

An internet block at Firewalla is a weaker test: packets have already crossed QNAP before the router filters them, and local traffic, multicast, and retries can continue. Blocking local traffic at the router also misses traffic switched entirely within the LAN. Firewalla explicitly documents [traffic continuing after an internet block](https://help.firewalla.com/hc/en-us/articles/16542636766611-Why-is-there-still-traffic-in-device-live-throughput-even-if-the-device-has-internet-blocked). AP7 isolation adds enforcement at the AP, but should not be assumed equivalent to making an endpoint silent; it also does not remove traffic already entering the switch from other paths.

If removing a device produces no recovery, that device is not automatically cleared: an already-failed receive/forwarding state might require a separate reset. A controlled startup with selected endpoints absent is a stronger later test, requiring a planned interruption. The successful WAN versus failing LAN comparison is meaningful, but also changes member ports, peer NICs, VLAN, and traffic; it does not isolate endpoint behavior by itself.

## Similar reports online

No exact QSW-L2110 case matching the observed approximately 237-second failure was found in the searches performed for this report.

- Juniper documents LACP timeouts under high CPU utilization as **PR1630201** in its [21.4R2 release notes](https://www.juniper.net/documentation/us/en/software/junos/release-notes/21.4/junos-release-notes-21.4r2/junos-release-notes-21.4r2.pdf). This establishes that implementation/resource problems can disrupt LACP; it does not establish the same cause on QNAP.
- A [firsthand 2021 failure report](https://www.reddit.com/r/networking/comments/m698ge/interesting_lacp_failure_yesterday/) describes a bundle with one member accepted by the switch while the Linux peer still listed both. Packet capture showed the peer had stopped transmitting LACP on the failing member; normal unicast traffic largely continued, while DHCP exposed the inconsistency. That report's missing speed/duplex and absent outgoing PDUs differ from this setup, where physical link parameters remain known and outgoing PDUs are captured locally. Its diagnosis is the author's interpretation, not a vendor-confirmed QNAP defect.

The [sanitized summary](evidence/lacp-single-group-20260905-summary.json) records the result and artifact hashes. Raw device identities, addresses, flows, notes, and captures remain ignored under `backups/lacp-single-group-20260905/`. No device block or endpoint disconnection was performed by the agent.

The user subsequently [disabled both IoT Wi-Fi networks](lacp-iot-wifi-isolation-20260905.md) while the watcher recorded. That separate five-minute observation also showed no recovery from the existing failure.
