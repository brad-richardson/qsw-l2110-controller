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
- Loop protection off plus reboot did not eliminate earlier failures. STP, EEE,
  storm limits, ingress/egress rate limits, IGMP snooping, DHCP snooping and ACL
  filters are disabled/empty in inspected settings. Current loop protection was
  restored on and reports zero violations. Flow control remains on.
- Brief downstream cuts, including ports 10+5+6 together, did not clear an
  already-established failure. Fresh isolated startup with port 10 disconnected
  has not yet been tested. No factory reset has been used as a controlled test.

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
