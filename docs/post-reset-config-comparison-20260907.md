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
ports. At that point neither had been performed in this controlled post-reset sequence; the combined follow-up below has since completed.
An incompletely configured peer triggering persistent switch state is a hypothesis,
not an established cause or evidence that the operator damaged the hardware.

[VLAN 10 probe evidence](evidence/linux-usb-vlan10-20260907.json).

[Sanitized comparison evidence](evidence/post-reset-config-comparison-20260907.json).
Related: [factory-reset 1+7 result](linux-usb-factory-reset-long-20260907.md).

## Follow-up: second configured group plus WAN VLAN membership also passed

The operator added group 1 / Long on unused ports 1+2 and moved those ports to
untagged VLAN 3999 in the same step. Group 4 / Long remained on 3+4.
Run `usb-ui-sweep-20260907T010659Z` completed a **360.029-second hold** with
**328.737 continuous clean reciprocal seconds**. Both members ended 61/61 at
1000/full, with zero link failures and partner churn. Both captures were complete
with zero drops. Cleanup finished at 01:13:02.999013 UTC without errors and left
the USB interfaces down.

Post-run readback `backups/second-lag-probe-readback-20260907T011330Z/` verified
both groups and VLAN 3999 on 1+2+9, VLAN 1 on 3–8, and VLAN 10 on port 10,
with matching PVIDs. The read session logged out successfully; no API access
occurred during observation. The combined change did not reproduce failure,
but it does not isolate the effects of adding the group versus changing VLAN
membership. A configured group on unplugged ports also remains distinct from
retained metadata on ports whose LAG mode is disabled.

[Second-group/VLAN evidence](evidence/linux-usb-second-lag-vlan3999-20260907.json).

## Remaining differences from the earlier production failure

The old USB bench layout above must not be confused with the production layout.
The saved production baseline
`backups/lan-ports-1-2-20260906T135419Z/before.json` had group 4 / Long on 3+4,
but VLAN 10 on **3–7 and 10**, VLAN 3999 on 1+2+9, and VLAN 1 only on 8.
The current 01:13 readback still has ports 3–7 on VLAN 1. The operator's next
proposed UI-only test moves those five ports to VLAN 10.

Other differences remain: that production baseline had mode-disabled ports 5+7
retaining group 5 / Short, and mirror destination 7 with all source directions
off; the reset-era values are group 0 and mirror destination 0. Production also
used Firewalla at 2.5 Gb/s, different actor identities and populated network
connections; these runs use Realtek USB peers at 1 Gb/s. Configured port settings
and LACP priorities match. The September 5 production snapshot instead had two
configured groups using Short, so there is no single universal production
baseline. VLAN names also remain empty rather than the old descriptive labels.

The [headless browser request comparison](ui-controller-request-comparison-20260907.md)
tests both the proposed VLAN move and a LAG group 4→5 change offline. It identifies
the disabled-port serialization difference but provides no evidence of a missing
automatic follow-up write. Live controller reapplication remains untested in this
controlled post-reset sequence.

## Production VLAN layout passed; controller reapply awaiting observation

The operator applied the production membership/PVID layout through QSS, retaining
group 4 / Long on 3+4 and group 1 / Long on 1+2. Run
`usb-ui-sweep-20260907T013218Z` completed **360.028 seconds** with **328.710
continuous clean reciprocal seconds**. Both members ended 61/61 at 1000/full,
with zero link failures or partner churn. Captures were complete with zero drops;
cleanup finished at 01:38:21.807506 UTC without errors, leaving both USB NICs down.
No switch API access occurred during the observation.

The subsequent authenticated readback verified VLAN 10 untagged/PVID 10 on
3–7 and 10, VLAN 3999 on 1+2+9, and VLAN 1 on 8. Thus the production VLAN
membership/PVID layout also permits a six-minute pass in this reset-era USB
setup. Peer/speed, disabled-port LAG metadata, mirror destination, and history
remain differences from the earlier failed production setup.

### Deliberate reapply through the controller client

After cleanup, a controlled one-shot harness used the controller's client and
configuration validation to resubmit the same values. This was **not ordinary
CLI apply**, which correctly produced an empty plan and would send no changes.
The LAG body was the previously audited 41-field controller payload with group
4 retained; VLAN entries used the controller's serializer for all three current
VLANs. It kept the same group numbers, timeout, VLAN names/memberships and PVIDs.

The harness checked model/build/MAC, the successful baseline and cleanup, USB
interfaces down/unaddressed, independent management on `eno1`, exact intended
configuration, a private native backup, and a refreshed pre-write snapshot.
Private artifacts are in `backups/controller-reapply-20260907T013852Z/`.

- LAG POST at 01:39:08.891882 UTC returned HTTP 200 with an empty body. All
  41 configuration fields matched on readback, and VLANs/PVIDs were unchanged.
- The following VLAN POST **timed out waiting for response headers** with an
  eight-second client timeout. It was not retried. The harness stopped before
  Save and successfully logged out.
- An independent read session verified the desired LAG/VLAN/PVID state and
  unchanged configured port/mirror settings. The VLAN timeout therefore does
  not establish a configuration mismatch or whether internal work occurred.
- A subsequent session verified the state again, completed Save, verified again,
  and logged out successfully. There was no second VLAN POST.

The two LAG runtime status fields changed from 1 to 0 after reapply; the USB peers
were already down from observer cleanup. This is not evidence of failed LACP.
No post-reapply negotiation observation has completed yet. Because the full
sequence included LAG, a timed-out VLAN request, and Save, any changed negotiation
result would require narrower controls before assigning the cause to one action
or the 18 disabled-port fields. Reboot persistence was not tested.

[UI production-layout baseline evidence](evidence/linux-usb-production-vlans-ui-20260907.json).
[Controller reapply evidence](evidence/controller-reapply-20260907.json).
