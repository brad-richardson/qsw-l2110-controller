# Public code and exact-image inspection — 2026-09-06

## Outcome

The exact QNAP image provides strong evidence for a **MaxLinear GSWIP / Zephyr implementation**, including LACP, LAG bridge-port/CTP mapping and packet-classification code. A Realtek switch SDK is therefore the wrong default research target; the image also has Realtek RTL826x PHY drivers, which does not establish a Realtek switching core. The precise silicon SKU is **not verified**. MaxLinear MxL86282S is a plausible family match (eight integrated 2.5G PHYs, two 10G uplinks, integrated web-smart processor), not an identification of this unit.

Public source exists for related MaxLinear Linux DSA interface/mapping and Open vSwitch LACP. I did **not** locate the exact QNAP/MaxLinear `f48x_app` Zephyr LACP source. Function names and diagnostics suggest an Open vSwitch-derived/adapted LACP implementation, but this is not a proven source/version match.

## Exact image evidence, reproducible offline

Local file: `/home/brad/dev/qsw-l2110-controller/backups/firmware/QSW-L2110-FW.v2.2.3_S20260713_100043.img`

SHA-256 recomputed: `4c9282b57e6d497623a6700c65c5a75bb33b19e504bf813155a2ae0ccb19b12b`.

Official origin: https://download.qnap.com/Storage/Networking/QSW-L2110/QSW-L2110-FW.v2.2.3_S20260713_100043.img

Only static `strings` was performed; no image execution, live hardware access, or configuration changes.

```bash
sha256sum backups/firmware/QSW-L2110-FW.v2.2.3_S20260713_100043.img
strings backups/firmware/QSW-L2110-FW.v2.2.3_S20260713_100043.img | rg 'WEST_TOPDIR.*(mxl|gswip|lacp|pce_rule|port_trunk)|CTP .*master CTP|LAG Pmapper|LAG updated|lacp status|LAG/LACP Pmap'
```

Selected embedded source paths (all begin `WEST_TOPDIR/`):

- `f48x_app/web/common/trunk/port_trunk.c`
- `zephyr/subsys/net/common/pce_rule_mng.c`
- `zephyr/subsys/net/l2/lacp/lag_update.c`
- `zephyr/subsys/net/l2/lacp/lacp.c`
- `zephyr/subsys/net/l2/lacp/lacp_shell.c`
- `zephyr/include/zephyr/net/mxl_bp.h`
- `zephyr/drivers/ethernet/eth_mxl.c`
- `zephyr/drivers/ethernet/eth_mxl_virt.c`
- `zephyr/drivers/ethernet/gswip/switchcore/src/gsw_pce.c`
- `zephyr/drivers/ethernet/gswip/switchcore/src/gsw_ctp.c`
- `zephyr/arch/arc/core/irq_manage.c`

Selected diagnostics adjoining `lag_update.c`:

- `Failed to refresh LAG Pmapper for BP port %u`
- `Pmapper of BP %u not setup`
- `CTP (%u) of BP ID (%u) is not master CTP (%u) of LAG (portmap %05x)`
- `LAG updated, group %05x, active ports %05x, changed ports %05x`

LACP diagnostics/function strings include `lacp_process_packet`, `lacp_slave_carrier_changed`, `send_lacp_pdu`, `compose_lacp_pdu fail`, per-port received/sent PDU messages, slave registration failures, maximum-instance errors, actor and partner state dumps.

**Potential future inspection capability:** the shipped image includes `lacp status [-c count]` and `trunk` shell commands; trunk output headings include CTP mapped BPIDs and LAG/LACP Pmap. This is evidence that diagnostic code is compiled into the image, **not proof an accessible/supported console exposes it**. Documented console availability remains to be investigated; no console access was attempted.

## Public primary sources and their actual relevance

1. MaxLinear MxL86282S official page: https://www.maxlinear.com/product/interface/ethernet/ethernet-switches/mxl86282s
   It describes the matching 8+2 port architecture and an integrated processor capable of an RTOS/web server. It links DSA/Host API software at git.maxlinear.com. This supports the family hypothesis only; QNAP has not been shown naming this SKU.

2. Upstream Linux MaxLinear source:
   https://github.com/torvalds/linux/blob/master/drivers/net/dsa/mxl862xx/mxl862xx.c
   https://github.com/torvalds/linux/blob/master/net/dsa/tag_mxl862xx.c
   The DSA driver explicitly programs CTP port assignments and bridge-port configuration. The tag driver uses an eight-byte CPU tag, derives a source port from its ingress/egress field and selects the receiving network interface by that port. These are concrete examples of why correct packet-to-port metadata matters. They are **Linux external-host drivers**, not the QNAP's Zephyr receive implementation or a demonstrated patch for this fault. Current inspected driver contains no LACP state machine or port_lag_join implementation to transplant.

3. Open vSwitch current LACP source:
   https://github.com/openvswitch/ovs/blob/main/lib/lacp.c
   Historical source also retrieved directly:
   https://raw.githubusercontent.com/openvswitch/ovs/branch-2.3/lib/lacp.c
   Current source renamed slaves to members; older source preserves names matching image strings. Receive processing looks up the local member, validates the PDU, marks that member current, resets its receive timer and learns the incoming actor as partner. Defaulting clears partner identity. Group key selection can use a member's configured key or port ID; a key matching a low-numbered port is therefore not itself proof of broken port mapping. There is no prerequisite that ports 2 or 3 negotiate first. These are upstream semantics and resemblance, **not proof of exact QNAP behavior**.

4. Primary OVS patch explaining a receive mapping failure mode:
   https://mail.openvswitch.org/pipermail/ovs-dev/2013-July/272944.html
   The historical patch makes receive/other calls return safely if passed an unknown slave while caller and LACP membership are out of sync. This illustrates a dropped-input path that would leave a member defaulted. **It does not establish this old defect exists on QNAP or is its cause.**

5. QNAP exact release notes:
   https://www.qnap.com/en/release-notes/qss/2.2.3/20260713
   Notes cover HTTPS management availability, learning MAC addresses from an unassigned VLAN, Qfinder traversal and password handling; no LACP fix is listed. Previous 2.2.1 notes discuss VLAN configuration stability but do not identify this LACP fault:
   https://www.qnap.com/en/release-notes/qss/2.2.1/20260417

6. QNAP's linked public GPL repository:
   https://sourceforge.net/projects/qosgpl/files/
   Its top-level listing exposes NAS GPL, QNE GPL, NAS CDDL, NAS toolchains and Misc GPL folders. I did not locate a QSW-L2110/QSS source package there or through targeted public searches. This is a search result, **not a claim that QNAP cannot provide one or that every archive was exhaustively searched**. MaxLinear's public software landing page redirected into an authentication flow; I did not authenticate. No large archive was downloaded.

7. QNAP product claim:
   https://www.qnap.com/en-us/product/qsw-l2110-10t
   Advertises LACP. No first-three-port-only restriction was found on that product page. This is not an exhaustive all-manuals restriction search.

## Mechanism implications and discriminating experiments

Observed persistent defaulted/zero-partner QNAP PDUs on a physically up port while Linux transmits are most naturally consistent with **that switch LACP member not accepting valid receive input**. Candidate locations now grounded in the image are packet classification / CPU delivery, ingress-to-virtual-interface/CTP mapping, stale member registration, or subsequent overwriting of receive state. Wire capture at the peer cannot distinguish these; even a perfect Ethernet TX capture does not prove switch CPU delivery.

A wrong/stale partner system ID alone is less compelling than receive-delivery/member-state trouble: in the inspected upstream implementation, a newly accepted valid PDU updates partner identity. It can matter indirectly via hardware classification or derived state, which remains untested.

The new 1+7 repeat changes actor identity and configure-to-peer delay (verified event timestamps show roughly 116 seconds instead of 0.092 seconds on its original sweep success). Since Slow LACP's receive lifecycle can expire/default before the peer starts, a bug on the empty/defaulted-group transition is a concrete hypothesis. It does **not** require 2/3 to be magic ports. A controlled immediate-vs-delayed startup experiment on the same 1+7 configuration, actor, cables and no preceding good pair would separate this from good-pair conditioning. Keep every successful hold longer than six minutes because earlier post-boot failures emerged only after 220–237 seconds.

The best future vendor request is source/diagnostic access for the exact `lag_update.c`, `lacp.c`, `pce_rule_mng.c`, and `eth_mxl_virt.c` build, plus per-member receive counts and CTP/BP/Pmapper tables before/after failure. That would distinguish an LACP protocol issue from delivery/mapping. No vendor contact was sent.

See also the [historical ordering analysis](historical-lacp-patterns-20260906.md) and [completed six-minute repeat](linux-usb-1-7-repeat-20260906.md).
