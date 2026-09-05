# Long baseline for all active LACP pairs — 2026-09-05

At the user's request, both configured active LACP pairs were changed from Short to **Long** and saved on QSW-L2110-10T firmware **2.2.3.20260713**. The controller completed successfully at 23:05:40 UTC. A complete readback immediately afterward and another after a 120-second settling interval matched the reviewed target. Final verification completed at 23:08:01 UTC.

| LACP group | Switch ports | Before | After |
|---|---|---|---|
| 1, Mac test pair | 1+2 | Short | Long |
| 4, production LAN pair | 3+4 | Short | Long |

Only `lacpTimeoutId_1` through `lacpTimeoutId_4` changed, from API value `0` to `1`. All other LAG fields, VLAN membership/names, independent PVIDs, and port configuration were identical to the pre-change snapshot. The disabled rows on ports 5 and 7 retain a stored group index, but they are not an active LAG and were preserved. Long is the selected experiment baseline; this work did not establish QNAP's factory default.

The supported controller apply path guarded the exact model/build, saved a private configuration backup, checked for drift before writing, applied the complete LAG table once, verified readback, requested save, and verified again. No firmware upload or reboot occurred. Persistence across a reboot remains untested. All QSS clients ran sequentially under the same local lock.

Wi-Fi remained the default route on `en0` in all 36 route samples. Source-bound probes to the gateway and internet each passed 175 of 176 attempts. Both failed once at 23:05:23 UTC during the apply, with successful samples immediately before and after separated by approximately 3.03 seconds. That is a bracket between successful observations, not an exact outage duration. No further failures occurred through 23:08:01. The final HTTPS check over Wi-Fi returned HTTP 200. The local monitor exited and stopped all its probe threads.

The production LAG's existing member problem persisted: QSS reported port 3 state `1` and port 4 state `0` both before and after, while both physical links remained 2500Mbps full duplex. This is limited switch status evidence, not a fresh packet/native Firewalla negotiation test. Long did not visibly repair the member status during this observation. No Firewalla configuration operation or production cable change was performed.

The Mac adapters remained standalone, connected to ports 1+2 at 1000Mbps full duplex, with no Mac bond present. Their QSS LAG member states remained `0`, as expected without an active Mac bond. No new Mac negotiation pass is claimed. The earlier [Short/Long experiment](macbook-timeout-results-20260905.md) remains the physical packet/native evidence.

The [firmware comparison preparation](firmware-comparison-preparation-20260905.md) was refreshed from the settled Long snapshot. Its source and planned configuration hashes are identical, and its timeout readiness list is empty. A new hardware-free rehearsal delivered all three verified images to the in-memory receiver in sequence, totaling 1,353 chunks. These are synthetic upload/reboot/LACP outcomes. Hardware execution remains disabled, and a fresh physical native/packet baseline plus the live capture/upload integration are still required before a firmware comparison.

The [sanitized evidence summary](evidence/lacp-long-baseline-20260905-summary.json) records exact changes, configuration hashes, probe timing, member states, and artifact hashes. Private snapshots, opaque backups, reviewed YAML/payload, controller log, connectivity records, refreshed comparison plan, and rehearsal remain in ignored `backups/lacp-all-long-20260905T2302Z/`. Earlier captures and Short snapshots were preserved. Historical example configurations still describe their original experiments; use this fresh baseline when preparing the firmware comparison.
