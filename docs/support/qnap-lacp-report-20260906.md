# QNAP support report draft — not submitted

Support route: [QNAP Service Portal](https://service.qnap.com/en-us/home).
Sign in with a QNAP ID, then create a support ticket. See
[QNAP's ticket instructions](https://www.qnap.com/en-uk/how-to/faq/article/how-do-i-open-a-technical-support-ticket-with-qnap).
No verified direct engineering-support email was found in this review.

## Suggested subject

QSW-L2110-10T: port-dependent LACP failure reproduced with Firewalla and independent Linux USB peer

## Copy/paste report

I am investigating an intermittent/port-dependent LACP failure on a QSW-L2110-10T,
hardware A0, QSS 2.2.3 build 20260713. The issue was also observed on 2.2.1 and
2.2.2 in controlled firmware comparisons. I have packet captures, native Linux
bond states, switch configuration readbacks, counters, and a full 28-pair sweep.

The switch keeps physical carrier at the expected full-duplex speed, but one
LACP member advertises Defaulted with zero partner identity. The other member
can remain synchronized. The peer captures outgoing LACP on the failed member;
this alone does not prove those frames reach the switch CPU. An ingress-mirror
experiment showed port-4 LACP disappearing from the mirror before the switch
declared its partner defaulted; working-port mirror controls succeeded.

Key observations:

- Firewalla at 2.5 Gb/s: ports 1+2 and 1+3 each sustained over ten minutes of clean
  reciprocal LACP plus approximately 2.35 Gb/s throughput tests. Moving the same
  second NIC/cable to port 4 or 8 failed to synchronize; returning it to a good
  port recovered without a router reboot.
- Independent Ubuntu Linux peer with two RTL8153/r8152 USB adapters at 1 Gb/s:
  all 28 unordered pairs of ports 1–8 were tested using one active group (group 4),
  QNAP Long, Linux Slow, and matching untagged/PVID VLAN 1. Only 1+2, 1+3, 1+7,
  and 2+3 initially negotiated. Other quick windows were negative; these are
  initial-negotiation screens, not equally long reliability tests.
- A subsequent 1+7 repeat failed throughout 360.030 seconds: zero clean
  two-member native samples out of 430, port 1 clean, port 7 defaulted. All 12
  switch-origin PDUs on port 7 had zero partner identity. Both links remained
  1000/full without link failures. Both captures had zero kernel drops.
- The earlier 1+7 success has fresh reciprocal synchronized PDUs in both
  directions on both correct ports. The repeat's exposed switch settings match,
  but the Linux actor identity and configuration-to-peer startup interval differ.
  We are investigating state/history dependence rather than assuming a fixed
  unsupported port set.
- After a later switch power cycle, a six-minute 1+7 observation using the same
  actor as the failed repeat initially synchronized, then QNAP port 7 defaulted
  189.063 seconds after peer setup and stayed failed. Port 1 remained clean;
  neither physical link failed, and Linux TX continued on port 7. No switch API
  polling or console/debug operations occurred during this observation. This
  excludes ongoing management polling as a necessary trigger, but not earlier
  state/history effects. See the [capture findings](../linux-usb-1-7-no-api-20260906.md).
- Loop protection off plus reboot did not eliminate earlier failures. STP, EEE,
  storm limits, ingress/egress rate limits, IGMP snooping, DHCP snooping and ACL
  filters are disabled/empty in inspected settings. Current loop protection was
  restored on and reports zero violations. Flow control remains on.
- Brief downstream cuts, including ports 10+5+6 together, did not clear an
  already-established failure. A later port-10 disconnection and peer restart
  also did not recover within a deliberately shortened 289-second observation.
  An isolated power cycle restored initial reciprocal negotiation, but that run
  stopped at 77 seconds and does not establish stability.
- A second full six-minute observation after reboot with port 10 connected
  reproduced port-7 defaulting at 189.025 seconds after peer setup, versus
  189.063 seconds in the earlier connected no-API run. Measured from the first
  QNAP port-7 PDU advertising 61/61, both intervals are 156.596 seconds. This
  suggests a repeatable timer/state sequence worth investigating; it does not
  identify the initiating event or component. See the [uplink controls and
  timing comparison](../linux-usb-uplink-controls-20260906.md).
- September 7 follow-up: after an operator-reported factory reset and only
  password, group 4 membership and Long-timeout setup, the same USB 1+7 actor
  and NIC mapping passed a full six-minute observation with 329.773 continuous
  clean seconds. Capture integrity and cleanup passed; physical links did not
  fail. Port-10 state is not yet confirmed, and reset/default settings were not
  independently read via API. This demonstrates improvement after reset, without
  identifying the responsible setting or internal state. See the
  [factory-reset result](../linux-usb-factory-reset-long-20260907.md).
- Moving the reset-era USB setup to 3+4 / group 4 Long also sustained 329.777
  continuous clean seconds in a full six-minute observation. An authorized
  post-run readback found test ports 1–8 still on the same VLAN 1/PVID 1 as
  before reset, but extra VLANs 10/3999 removed, ports 9/10 on VLAN 1, inactive
  LAG group/timeout fields cleared, and a disabled mirror destination cleared.
  Other inspected protection/port settings match. These are separate unisolated
  differences; we have not established VLANs or the inactive LAG fields as the
  cause. See the [configuration comparison](../post-reset-config-comparison-20260907.md).

A useful reproduction environment is an isolated two-NIC Linux 802.3ad peer,
untagged matching VLANs, group 4 Long, and at least six minutes of simultaneous
per-member LACP/native capture. Compare a known-good 1+3 pair with 1+7 or 1+4;
record the actor identity and exact configuration/link/startup ordering. Port 7
is intermittent and the full sequence may matter; I cannot promise every fresh
unit/configuration will reproduce immediately.

Could engineering provide:

1. Any restrictions or known defects involving LACP member ports, group IDs,
   master-port mapping, or empty/defaulted-group transitions on this firmware?
2. A supported way to inspect per-member LACP receive counts and CTP/bridge-port/
   Pmapper state before and after failure? Static inspection of the official
   image includes lacp status and trunk diagnostics, but console availability
   has not been established.
3. Guidance or a diagnostic build to distinguish ingress classification/CPU
   delivery from LACP state-machine behavior? Image paths include lag_update.c,
   lacp.c, pce_rule_mng.c and eth_mxl_virt.c.
4. Supported access to the shipped Zephyr UART shell? The image includes
   shell_uart_backend, UART_0 and a uart:~$ prompt; your hardware specification
   lists an RJ45 console. Please confirm connector location, electrical standard,
   pinout, serial settings, authentication and any production-firmware restriction.
5. The mapping of the diagnostic page's USXGMII interfaces 0/1 to this model's
   physical/internal ports, and a safe register list for XPCS/PHY status reads?
   We have inspected the UI serialization but have not assumed those selectors
   are front-panel port numbers or started the hidden cable/eye tests. A live
   check returns phy_eye_test=true and serves both diagnostic pages; cable-result
   GET returns 0xF sentinel values. Register-read execution remains untested.

I can supply the sanitized reports below and selected packet captures on request.
Please advise which additional diagnostic collection would be most useful.

## Prepared evidence

- [Full sweep](../linux-usb-sweep-results-20260906.md)
- [Six-minute repeat](../linux-usb-1-7-repeat-20260906.md)
- [Historical review](../historical-lacp-patterns-20260906.md)
- [Firmware/code investigation](../public-lacp-code-20260906.md)
- [Current settings](../evidence/qss-settings-audit-20260906.json)
- [Native diagnostics and console investigation](../native-diagnostics-console-20260906.md)

Raw backups contain credential material and are not part of this draft. Packet
captures remain local; review the selected files before attaching them. No
support ticket, email, or attachment has been sent.
