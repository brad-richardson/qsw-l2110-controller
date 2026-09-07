# Native QSS Apply versus controller requests — September 7, 2026

Headless Chrome exercised the shipped QSS forms for **both VLAN and LAG changes**.
The VLAN JSON matched the controller exactly. Moving ports 3+4 from group 4 to
group 5 matched every shared LAG field, but the controller included 18 additional
disabled-port fields. This confirms the previously documented serialization
difference during an actual browser Apply interaction; it does not establish
that those fields cause the hardware failure.

## Method and limits

This was an **offline browser experiment**, not a live switch reconfiguration.
All requests were intercepted, using the 01:13:32 post-run snapshot as fixtures.
POSTs received synthetic HTTP 200 `{"status":"success"}` replies. No session
was opened on the switch. Both pages loaded and their Apply buttons were clicked
without page errors or missing resources. Each Apply was observed for 6.5 seconds.

The LAG/VLAN HTML, their JavaScript, `myajax.js`, and `comm.js` were byte-for-byte
located in firmware 2.2.3's image. Hashes and offsets are in the evidence. This
validates the client-side code used; simulated responses cannot establish real
backend acceptance, hardware side effects, or every error-dependent callback.
The VLAN fixture remains the original snapshot after the intercepted write;
its refresh is request-sequence evidence, not verification of applied state.

Private captures, screenshots, full request logs and the exact harness are under
`backups/ui-request-audit-20260907T012128Z/`. An earlier attempt captured LAG but
stopped before VLAN Apply because its button selector was ambiguous; the complete
run used a modal-scoped selector.

## Compared changes

| Case | Native UI request | Controller comparison |
| --- | --- | --- |
| LAG: move 3+4 from group 4 to 5; preserve group 1 on 1+2 and Long on both groups | POST `/port_trunk_cfg.json`, 23 string-valued fields | All 23 identical; controller sends 41 fields |
| VLAN: move 3–7 from VLAN 1 to VLAN 10; retain 8 on VLAN 1, 1+2+9 on VLAN 3999 and 10 on VLAN 10 | POST `/tag_vlan.json` | Exact parsed JSON equality, including types, array order and reserved port element |

The 18 extra LAG fields are priority `128`, timeout `0` and group `0` for each
mode-disabled port 5–10 in this snapshot. The browser disables those controls
and `FormData` excludes them. Earlier sweep states retained nonzero groups/Long
on disabled ports, so their extra values can differ from this reset-era case.
The group change itself is simply `Port_3_grpInd` and `Port_4_grpInd` from
`"4"` to `"5"`; no additional group-delete/create request was emitted.

For VLANs both implementations send VLAN 10's new membership first, then
VLAN 1's reduced membership, with `deletedVlans: []`. Neither sends a separate
PVID write for this action. The controller plans expected PVID changes and
verifies the resulting state.

## Requests after Apply

- LAG: periodic GETs for `/port_stats.json` and `/port_trunk_refresh.json`.
- VLAN: immediate GET `/tag_vlan.json` to reload the VLAN stream, plus routine
  status/session GETs.
- Neither emitted another configuration POST within the observed window.

Source inspection agrees: the LAG callback `submitbtn_act()` only logs its
response; the VLAN callback reloads VLANs. Global Save separately invokes
`ajaxSaveAllConfigs()` via `handleSaveClick()` and POSTs `/save_all_configs.json`.
The controller explicitly saves after verification. Thus the inspected UI Apply
path does not reveal a missing automatic commit request; UI Apply and the full
controller workflow still differ in explicit Save and readback behavior.

## Historical payload check and next discriminating test

A bounded audit of 31 saved, non-null LAG plans from the 18:32:51 and 19:01:01
September 6 sweeps found all 41 expected fields, no range/enum violations, and
matching saved after-readbacks for every supplied field. This is not an audit of
every historical write, nor proof of internal state correctness. The loopback
emulator requires the full 41-field table, so it cannot independently resolve
the native UI's omission semantics.

After the planned UI-only production VLAN-layout run, a useful experiment is a
logged LAG reapply comparing the native 23-field body with the controller's
41-field body, holding group, timeout, peer, VLANs and Save policy constant.
A group-number transition can also be tested in both paths, with observations
after each transition. Do not attribute a result to omitted fields if the group
or Save behavior also changes between the compared runs.

Ordinary controller apply against an identical desired configuration produces
an empty plan and sends no change: that would not test reapplication. No forced
reapply or production controller behavior change was performed in this audit.

[Request/payload evidence](evidence/ui-controller-request-comparison-20260907.json).
[Historical payload audit](evidence/historical-lag-payload-audit-20260907.json).
[Earlier rendered-form audit](qnap-ui-audit-20260905.md).
