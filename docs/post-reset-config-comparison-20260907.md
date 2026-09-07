# Post-reset 3+4 success and configuration comparison — September 7, 2026

## 3+4 passed the six-minute observation

After the successful reset-era 1+7 run, the operator moved the same USB NICs to
3+4 and configured group 4 / Long through QSS. The same Linux actor
`02:3f:6c:3a:ff:a7` and member order were retained. No switch requests occurred
during observation. Run `usb-ui-sweep-20260907T001856Z` completed its
360.018-second hold with **329.777 continuous clean reciprocal seconds**.
Both native members ended at 61/61, 1000/full, zero link failures and no partner
churn. Both PCAPs were complete with zero kernel drops; host cleanup finished
at 00:25:00.991500 UTC without errors and returned both USB NICs down.

This pair failed the earlier USB sweep's initial-negotiation screen. Older
Firewalla 3+4 reboot experiments did show transient negotiation; this result
should not be called its first-ever synchronized packet. It is a sustained
USB negotiation result, not per-member forwarding or production validation.

[3+4 capture evidence](evidence/linux-usb-factory-reset-3-4-20260907.json).

## Authorized post-run readback

After capture shutdown, the operator requested an authenticated configuration
dump and comparison. One session verified the same QSW-L2110-10T A0, MAC
24:5e:be:77:e5:86, firmware 2.2.3.20260713; read LAG, VLAN/PVID, port and
additional settings; downloaded the opaque native configuration backup; and
logged out with HTTP 200. No configuration, time, diagnostic-test, or Firewalla
settings were changed. Raw results and the potentially credential-bearing
backup remain private in `backups/factory-reset-config-20260907T002540Z/`.

The principal baseline is the failed 1+7 repeat's
`backups/usb-sweep-20260906T192611Z/original-state.json`. Its VLAN configuration
also matches the later 23:03 preflight before a delayed failure. Additional
controls are compared against `backups/qss-settings-audit-20260906T194559Z/`.
The snapshots are at different times and have different active pairs; this is
a configuration comparison, not an experiment isolating any one difference.

| Setting | Pre-reset bench | Post-reset readback |
| --- | --- | --- |
| VLAN inventory | 1 (`default`), 10 (`lan`), 3999 (`wan-transit`) | Only 1 (empty name) |
| Ports 1–8 | Untagged VLAN 1, PVID 1 | Same |
| Port 9 | Untagged VLAN 3999, PVID 3999 | Untagged VLAN 1, PVID 1 |
| Port 10 | Untagged VLAN 10, PVID 10 | Untagged VLAN 1, PVID 1 |
| Active LAG | Group 4 / Long on 1+7 | Group 4 / Long on 3+4 |
| Inactive LAG fields | Ports 2–6 and 8 retained group 4 / Long with mode disabled | Every nonmember has group 0 / Short with mode disabled |
| LACP priorities | System 32768; ports 128 | Same |
| Port admin/speed/flow/EEE settings | All enabled, Auto, flow control On, EEE inactive | Identical on all ten ports |
| Mirror destination | Port 7, all source directions disabled | Destination 0, all source directions disabled |

Loop configuration/status, STP, storm control, DHCP snooping, separate EEE
settings, ACL list, QoS port mode and IGMP configuration exactly match the
September 6 audit responses. This does not cover every hidden setting or
switch-internal table. The current hidden port-VLAN view reports frame type 0
on all ports, matching the older September 5 view's frame-type fields; that
older view predates the USB bench and must not supply its PVID baseline.

Port 10 reported a 2500/full link during this readback. That confirms current
connectivity, not its uninterrupted state during either completed capture.

## What this says about VLANs and the controller

**VLAN configuration is a real difference, but there was no test-member VLAN
mismatch in the earlier USB bench.** Ports 1–8 already shared untagged VLAN 1
and PVID 1. A VLAN-related cause would involve the wider inventory, other ports,
or internal mapping/state behavior, rather than simply fixing 3 versus 4's PVID.

The inactive LAG fields are another concrete difference. In
`src/qsw_l2110/reconcile.py`, member removal copies the existing LAG table and
sets only `portTypeId_N=0`; it preserves that port's group, priority and timeout.
This explains how disabled ports can retain group 4 / Long during a sweep.
Those fields are expected to be inactive, but their hardware effect has not been
verified. This is neither proof of a controller defect nor proof that QSS safely
ignores the retained values. The former bench also produced some successful
pairs with retained fields, so they are not sufficient to explain every result.
No controller behavior was changed during this review.

The current working configuration also still reports SNTP enabled, poll value
64, server `pool.ntp.org`, timezone -05:00 and DST disabled. Thus the one-hour
display offset is compatible with the successful run. The readback does not
establish when SNTP last synchronized or whether earlier clock steps occurred.

Useful controls preserve this private known-good backup and change one dimension
at a time through QSS. Both the VLAN 3999-only probe and subsequent VLAN 10
restoration below have now passed. Inactive LAG metadata, an additional configured
group, and the disabled mirror destination remain separate controls to investigate.
Do not restore all old settings together and attribute any failure to VLANs alone.

## Follow-up: restoring VLAN 3999 on port 9 did not reproduce failure

Run `usb-ui-sweep-20260907T003542Z`, group 4 / Long on 3+4 with the same actor
and NIC mapping, completed a **360.028-second hold** with **329.777 current
continuous clean seconds**. Both members remained 1000/full, 61/61, without
link failures or partner churn. Both PCAPs were complete with zero kernel drops;
360 native samples had a maximum gap of 1.010 seconds. Host cleanup completed
at 00:41:46.649082 UTC without errors, leaving both USB interfaces down.

The user indicated the next test was running after the VLAN 3999 proposal, but
did not separately answer the detailed configuration question. A post-run
authenticated readback at `backups/vlan-probe-readback-20260907T004228Z/`
verified the actual state: VLAN 3999 untagged on port 9 with PVID 3999, VLAN 1
untagged on ports 1–8 and 10 with PVID 1, and no VLAN 10. LAG, configured port
settings and mirroring match the preceding working post-reset snapshot exactly.
The new VLAN's name is empty, unlike the old `wan-transit` label. No API session
was opened during observation; the post-run readback made no configuration
changes and logged out with HTTP 200. It verifies the state after the capture,
not an independently sampled configuration history throughout the run.

Thus this partial restoration of the old VLAN layout was compatible with another
successful six-minute observation. It does not eliminate interactions involving
VLAN 10/uplink placement, inactive LAG fields, or sequence/boot history, and it
does not validate forwarding or long-term reliability. The full old VLAN layout
had not yet been restored at this point in the sequence.

[VLAN 3999 probe evidence](evidence/linux-usb-vlan3999-20260907.json).

## Follow-up: VLAN 10 and the old bench membership/PVID layout also passed

The operator added VLAN 10 and ran the same group 4 / Long observer on 3+4.
Run `usb-ui-sweep-20260907T005541Z` completed its **360.029-second hold** with
**329.745 continuous clean seconds**. Both members ended at 61/61, 1000/full,
zero link failures and no partner churn. Both complete captures had zero kernel
drops; there were 360 native samples with a maximum gap of 1.010 seconds.
Cleanup at 01:02:32.187174 UTC returned both USB NICs down without errors.

Post-run readback `backups/vlan10-probe-readback-20260907T010256Z/` verified:

- VLAN 1 untagged/PVID 1 on ports 1–8;
- VLAN 3999 untagged/PVID 3999 on port 9;
- VLAN 10 untagged/PVID 10 on port 10;
- unchanged LAG, configured port and mirror settings versus the previous pass.

VLAN IDs, every port membership, and every port PVID now match the pre-reset
USB bench snapshot. VLAN names remain empty rather than the old descriptive
labels. No API session occurred during observation, and the subsequent readback
made no configuration changes and logged out with HTTP 200.

Restoring this VLAN/PVID layout in the reset-era 3+4 setup therefore did not
reproduce the failure within six minutes. This weakens the layout-alone theory;
it does not test every boot/order interaction or the old active pair with that
layout. Inactive LAG metadata and the disabled mirror destination still differ.

The operator proposed adding a second configured LAG on unused ports 1+2 as the
next control. The suggested test is separate group 1 / Long on unplugged 1+2,
preserving group 4 / Long on 3+4 and rerunning the same observer without reboot.
This is **not** the same test as leaving group/timeout values on mode-disabled
ports. Neither has been performed in this controlled post-reset sequence yet.
An incompletely configured peer triggering persistent switch state is a hypothesis,
not an established cause or evidence that the operator damaged the hardware.

[VLAN 10 probe evidence](evidence/linux-usb-vlan10-20260907.json).

[Sanitized comparison evidence](evidence/post-reset-config-comparison-20260907.json).
Related: [factory-reset 1+7 result](linux-usb-factory-reset-long-20260907.md).
