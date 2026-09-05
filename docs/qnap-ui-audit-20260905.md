# QNAP UI and configuration audit, 2026-09-05

Read-only inspection of QSW-L2110-10T running QSS 2.2.3.20260713.
No switch configuration changes or cable/PHY tests were performed.

## Capture coverage

Private artifacts are in `backups/qnap-ui-audit-20260905T031837Z/`.
They include downloaded vendor HTML/JavaScript, direct JSON reads, and
16 page screenshots with rendered HTML and form/control state. Browser
responses and manifests record which reads succeeded. Authentication
cookies and request headers were not included in these new captures.

Two earlier HAR files contained port-settings and account pages plus
polling, but not the LAG page or most other settings. Their incompleteness
is established; the original Chrome export behavior that caused it is not.

The new capture used a separate headless Chrome context, the downloaded
static assets, and live data reads. Requests were serialized and all
configuration writes blocked. Both browser sessions were closed afterward.

## Findings

| Area | Verified state / limitation |
|---|---|
| LAN LAG | Ports 3+4, LACP, group 2, priority 128 each, Short timeout, system priority 32768; matches the controller |
| LACP controls | UI offers mode, group, per-port/system priorities, and timeout; no active/passive, minimum/maximum active members, or transmit-hash selector was found on that page |
| Physical ports | Both LAN members show enabled, auto-negotiated 2.5G full duplex, flow control on |
| Loop protection | Detection and prevention enabled; no violations reported; displayed shutdown time is 3 seconds, decoded from raw `recover_time=6` in half-second units |
| STP | Read endpoint reports disabled; visible page offers loop protection rather than an STP editor |
| EEE | Disabled and inactive on all ten ports |
| Storm control | All four categories disabled on all ports |
| QoS rate limits | Ingress and egress limits zero/off on all ports |
| DHCP snooping | Disabled |
| IGMP snooping | Disabled |
| ACL | Hidden page's read endpoint reports zero entries |
| Static MAC entries | Empty |
| VLAN frame admission | Ports 3+4 have PVID 10 and Frame_Type 0 (All), matching the native LAN |
| Port mirroring | Ingress, egress, or both selectable, with source and destination ports; currently disabled |

### Native UI versus controller payload

The browser's unsubmitted LAG form contains 23 fields. The controller's
complete payload contains 41. Every shared key/value matches, including all
fields for the four configured LACP ports and all disabled-port mode values.

The 18 extra controller fields are the saved priority, timeout, and group
for ports 5-10 where LAG is disabled. The browser disables those controls,
so `FormData` omits them. This contradicts an assumption that the controller
exactly reproduces the UI payload, but does not establish that its extra
fields cause the negotiation failure. No apply was attempted in this audit.
The comparison is saved as `ui-controller-comparison.json`.

### Status interpretation

The LAG table calls port 4 "Link Down" while the same screen's physical
port graphic is green and port settings report 2500 Mbps. The bundled help
text describes the lowest-numbered group member as the mapped bridge port.
The meaning of the LAG table's per-port status is therefore not sufficiently
established to use as physical carrier or collecting/distributing evidence.

This limits earlier claims based on QNAP's status field. It does not erase
the independent Firewalla states, defaulted partner LACPDUs, and unsuccessful
single-member connectivity test.

### Hidden pages and capture limitations

- ACL, LLDP, and port-based VLAN entries are commented out of the menu.
  Their shipped source does not establish supported features or safe tunables.
- The hidden port-based VLAN GET succeeds, but the page throws a TypeError
  when reading a missing `All` translation and renders only part of its
  table. Its raw JSON is complete; its screenshot is deliberately preserved
  as an incomplete render. We did not repair or submit this hidden UI.
- The first browser pass blocked that hidden page's GET until its semantics
  were confirmed from source; the second pass allowed the verified read.
- One startup `/status.json` request failed during the first pass. The
  targeted LAG/settings reads succeeded. A manifest's `populated` flag
  means the page shell loaded, not that every control is trustworthy.
- No dedicated LACP actor/partner detail page, per-member LACP RX counter,
  or LACP state-machine log was found in the inspected menu and AJAX code.
  This does not prove that firmware/support diagnostics lack such data.

## Useful next tests, deferred

1. Reapply or recreate the same QNAP LAN LAG through the native UI to
   eliminate the controller's disabled-field serialization difference.
2. If needed, perform one controlled loop-protection toggle or switch the
   group's LACP timeout from Short to Long. These are experiments, not
   identified corrections. No violation is currently reported, and differing
   peer timeout requests alone are not an LACP misconfiguration.
3. The subsequent [ingress-mirror test](lacp-diagnostics-20260905.md) used a
   directly connected laptop: zero LACPDUs from failing port 4, versus 31
   from working port 3. Mirroring was disabled afterward. The working-port
   control succeeded, but handling of an unsynchronized LAG member remains
   unverified, so missing mirrored packets do not alone prove physical loss.

Screenshots alone cannot show whether QNAP's LACP state machine receives
and accepts Firewalla's packets. A native-UI control experiment and a
validated capture point would add stronger evidence than more cable swaps.

The reusable capture tool is now in `tools/capture_qss_ui.py` and its original
Node helper. A fresh hardware run captured the LAG page with 37 recorded
responses, 42 native controls, and no blocked requests. See
[diagnostic tool usage](diagnostic-tools.md). The dynamic-MAC reader was also
corrected to follow pagination; its previous first-page-only result omitted
most clients. A complete read found 60 entries and correctly located the
capture laptop on port 7.
