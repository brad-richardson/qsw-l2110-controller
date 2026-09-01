# Hardware validation plan

This plan is intentionally conservative. Its first goal is to prove the private
interface without risking an Internet-to-LAN bridge. Its second goal is to test
whether the QSW-L2110 distributes flows well enough for a double-LACP Firewalla
Gold Plus topology.

Record the date, model, firmware build, switch configuration checksum, observed
response shapes, and result of each phase. Do not publish credentials, cookies,
login digests, MAC addresses, public addresses, or configuration backups.

## Required lab state

- The real ONT is physically disconnected.
- The office production trunk is physically disconnected.
- A laptop is connected directly to port 5 as the rescue path.
- Only one cable of any proposed LAG is connected until both endpoints are configured.
- The current QSS configuration has been downloaded through the web UI.
- The switch is on the latest applicable QSS 2.2.x firmware.

The documented DHCP fallback address is `169.254.100.101`; use a laptop address
such as `169.254.100.102/16` if no DHCP server is present.

## 1. Baseline and read-only interface

1. Change the default password and enable HTTPS in QSS.
2. Record the exact model and firmware shown by QSS.
3. Run `about`, `dump-lags`, and `dump-vlans`.
4. Confirm `about` agrees with the QSS UI.
5. Confirm both dump commands make no visible configuration change.
6. Confirm VLAN GET completes or reaches a harmless stream timeout after returning all entries.
7. Compare every returned LAG, VLAN membership, and PVID with QSS.
8. Download a CLI backup and compare its size and SHA-256 with a web-UI backup.

Expected firmware-derived endpoints:

| Operation | Endpoint |
|---|---|
| Identity | `GET /get_model_name.json`, `GET /status.json` |
| LAG configuration/state | `GET /port_trunk_cfg.json`, `GET /port_trunk_refresh.json` |
| VLAN membership/PVID | SSE `GET /tag_vlan.json`, `GET /all_port_pvid.json` |
| Backup | `GET /config/download` |

Stop if the model, field names, array indexing, authentication behavior, or
backup format differs from [api-notes.md](api-notes.md). Update fixtures before
enabling writes.

## 2. Disconnected LAG canary

Use two disconnected spare 2.5G ports, preferably ports 6 and 7.

1. In QSS, put ports 6 and 7 into an otherwise unused LACP group.
2. Capture the browser request body and the before/after `dump-lags` output.
3. Confirm the POST is flat while the GET response nests values under `Port_N`.
4. Remove the test group in QSS and verify the original state returns.
5. Create a temporary YAML file managing only ports 6 and 7.
6. Run `plan`; confirm no unrelated port appears in its diff.
7. Run `apply` with both ports still physically disconnected.
8. Confirm QSS shows the canary group and that a second `plan` says `No changes.`
9. Remove the canary through a second declarative apply and verify again.

Stop if applying a LAG changes any unmanaged port, loses the rescue connection,
or cannot be verified through a fresh GET.

## 3. Disconnected VLAN canary

1. Through QSS, create VLAN 4093 named `api-canary` with port 8 tagged.
2. Capture the browser POST and SSE read-back.
3. Confirm `port_states` is 1-indexed with element zero reserved, and values are
   `0=not member`, `1=untagged`, `2=tagged`.
4. Delete VLAN 4093 in QSS and verify VLAN 1/PVID state is unchanged.
5. Repeat creation with a temporary YAML file and `apply`.
6. Confirm no unlisted VLAN was altered and a second `plan` is empty.
7. Delete the canary manually; declarative deletion is intentionally not implemented yet.

Also verify whether VLAN membership is displayed only on physical LAG member
ports, as the 2.2.3 firmware UI suggests. Until proven otherwise, keep all member
ports identical; the configuration validator enforces this.

## 4. Build the intended switch configuration offline

1. Keep all four Firewalla cables and both 10G edge cables disconnected.
2. Adapt `examples/firewalla-gold-plus.yaml` to the actual LAN VLAN IDs.
3. Confirm VLAN 1 explicitly removes ports 1, 2, and 9.
4. Confirm the WAN-transit VLAN contains exactly ports 1, 2, and 9, all untagged.
5. Confirm the LAN native VLAN contains ports 3, 4, 5, and 10, all untagged.
6. Confirm every internal LAN VLAN is tagged on ports 3, 4, and 10 only.
7. Run `plan`, inspect every line, then run the gated `apply`.
8. Reboot the switch and confirm `plan` remains empty.
9. Export and checksum the known-good configuration.

## 5. Firewalla and link-state validation

1. Configure the Firewalla WAN and LAN LAGs in Router Mode using its supported app.
2. Connect one member of each group.
3. Confirm the correct VLAN and basic WAN/LAN path before adding redundancy.
4. Connect the second member of each group.
5. Verify all four members are collecting/distributing in QSS and, read-only,
   in `/proc/net/bonding/*` on Firewalla.
6. Pull each member individually and verify traffic continues.
7. Reconnect it and confirm it rejoins the correct aggregator.

Do not modify Firewalla-managed files through SSH.

## 6. Isolation tests

Perform these before attaching the real ONT:

1. Substitute a test host for the ONT on port 9.
2. With Firewalla disconnected, verify the port-10/office side sees no DHCP,
   IPv6 router advertisements, ARP, or unicast from the simulated WAN.
3. Verify the simulated-WAN host cannot reach LAN hosts or the QSS web interface.
4. Verify the rescue host on port 5 can still reach QSS.
5. Run a packet capture on both sides while generating broadcasts.
6. Reboot the switch and repeat the negative tests before reconnecting Firewalla.

The management-plane test is mandatory: QNAP does not document a dedicated
management-VLAN binding for this Lite Managed model.

## 7. Distribution and throughput tests

LACP is per-flow aggregation, not a single 5 Gb/s pipe.

1. Run one `iperf3` flow in each direction; expect no more than one member's rate.
2. Run `iperf3 -P 4` and `-P 8`, then repeat with multiple source/destination pairs.
3. Watch every physical member's byte counters during each test.
4. Confirm both members carry substantial traffic in both directions.
5. Repeat through the real ISP handoff only after isolation tests pass.
6. Record DHCP/static, PPPoE, and ISP-VLAN behavior separately.
7. Repeat with Firewalla QoS/Smart Queue enabled and disabled.

The decisive result is QNAP-to-Firewalla WAN distribution. If parallel flows
still pin to one member, the design cannot provide the intended downstream aggregate.

## 8. Recovery and fail-open test

Only with the ONT physically disconnected:

1. Verify the known-good backup restores through QSS.
2. Verify the switch reboots and the CLI plan returns clean.
3. If factory-default behavior is tested, prove that all edge ports are treated
   as one broadcast domain and document the resulting fail-open risk.
4. Restore the known-good configuration and repeat isolation tests.

## Evidence record

| Item | Result |
|---|---|
| Model and firmware guard | Pending |
| Login/cookie behavior | Pending |
| Read-only LAG response | Pending |
| VLAN SSE response and termination | Pending |
| Backup parity | Pending |
| LAG canary/read-back | Pending |
| VLAN canary/read-back | Pending |
| Save and reboot persistence | Pending |
| WAN/LAN negative isolation | Pending |
| Management-plane isolation | Pending |
| Per-flow and parallel-flow distribution | Pending |
| Member failure/rejoin | Pending |
| Backup restore | Pending |
