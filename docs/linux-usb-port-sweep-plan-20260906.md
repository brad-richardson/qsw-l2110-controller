# Independent Linux USB-NIC port sweep — proposed September 6, 2026

**Preparation only. No production teardown, Firewalla change, Linux test bond,
or new sweep has been performed.** The user is considering removing QNAP from
production, temporarily retiring the production LAG, and using the two USB NICs
on this Linux observer for an independent switch test.

Current production remains **QNAP 1+3, group 4 Long**, Firewalla Slow. The
[completed 1+3 result](lan-ports-1-3-results-20260906.md) passed 678.574 seconds
of joint native/fresh-PDU evidence plus 2.35 Gb/s bidirectional throughput.
All owned recorder/server/watcher processes are stopped. Ports 4 and 8 failed
in prior controls, and returning the same NIC/cable to port 2 recovered LACP.
2+3 has not yet been tested; do not describe every pair among 1/2/3 as proven.

## Host readiness

Read-only checks found:

- `eno1` is the existing routable management interface, managed by networkd.
- Linux kernel 7.0.0-31 has `bonding.ko.zst` available; no test bond was created.
- No USB Ethernet adapters were visible in `lsusb` or the network interface list.
- Actual unsandboxed `sudo -n true` reported that a password is required. A
  reviewed local administrator command will be needed for bond/namespace creation
  and packet captures; do not request or store the user's password in chat.

The user may plug the adapters into USB now, leaving Ethernet disconnected
until configured. Identify both adapters by USB ID, driver, permanent MAC,
interface name, and USB bus speed before writing setup commands. Confirm both
report the same full-duplex Ethernet speed and support native Linux bonding.
Do not assume Mac interface names or adapter identity carry over to this host.

Linux 802.3ad requires driver support for reporting speed/duplex and a matching
switch configuration. The initial peer settings should be 802.3ad, Slow LACP,
MII monitoring (for example 100 ms), MTU 1500, and a recorded stable bond identity.
Use QNAP Long throughout the first sweep. Source:
[Linux bonding documentation](https://docs.kernel.org/networking/bonding.html).
If these are the same 1 Gb/s Mac adapters, their 1 Gb/s results will not by
themselves establish behavior at the production 2.5 Gb/s speed.

## Separate production before resetting the switch

1. Inventory the replacement production path and all QNAP downstream branches.
   The Tenda TEM2010X is in Standard mode; its uplink was on copper port 8, but
   not every attached port is inventoried. Establish available copper ports
   before prescribing cable destinations. Avoid the suspect SFP path.
2. **Disconnect one Firewalla LAG member before removing the LAG or changing
   the router to ordinary LAN ports.** Do not bridge two live cables into the
   same LAN during conversion; an earlier teardown produced a bridge loop.
3. Coordinate a single-cable Firewalla LAN connection and migrate necessary
   downstream links to the production switch. Confirm this host's `eno1`,
   gateway, internet, APs, and other required clients work without the QNAP
   forwarding their production traffic. This is a future coordinated change,
   not authorization to alter Firewalla in the current test session.
4. Preserve the saved QNAP configuration and current evidence before changing
   its bench layout. Keep an independent QNAP management connection, initially
   on port 10, and record its VLAN/IP path. Avoid relying on a test LAG for
   management. A management-only connection can remain while QNAP is out of
   the production forwarding path.

## Test harness and controls

Use only the two identified USB interfaces in a dedicated temporary Linux
network namespace/bond. Leave `eno1`, its routes, Docker bridges, and Tailscale
untouched. Prevent automatic DHCP/default-route setup on the test interfaces.
LACP negotiation itself needs no IP address or internet connection; an isolated
bench VLAN can carry the test group while management stays separate.

Capture on each physical USB member (non-promiscuous initially) and sample
privileged native bond state about once per second. Record speed, carrier,
errors, actor/partner state, identities, advertised key, and group membership.
Classify each result as no carrier, never synchronized, initially synchronized
then expired/defaulted, sustained clean, or forwarding failure. Native carrier
or aggregate member count alone is not proof that both members distribute.

First establish a Long/Slow **1+2** control with these exact adapters and this
Linux peer. If that fails, debug the harness before blaming additional ports.
The earlier Mac Long result is useful context, not a substitute for this control.

Hold cables, adapter assignments, firmware, configured group number, VLAN,
speed, timeout, and background topology fixed through the first comparison.
Record all changes explicitly. A switch reboot is a separate variable; link
flaps alone may leave hardware state intact.

## Sweep order

| Phase | Pair(s) | Purpose |
|---|---|---|
| Harness control | 1+2 | Establish a known-good independent peer baseline. |
| Fixed port-1 sweep | 1+3, 1+4, 1+5, 1+6, 1+7, 1+8 | Change one physical port while retaining lowest member 1/key expectation. All production branches must first be removed from these ports. |
| Lowest-member controls | 2+3, then selected pairs such as 3+4 and 7+8 | Test whether behavior depends on the anchor, advertised key, or pair composition. |
| Adapter/cable reversal | Repeat representative good and bad pairs with adapters exchanged | Determine whether the failure follows a switch port or the USB path. |
| Startup-state control | Repeat the same good and bad configurations after a switch reboot | Separate retained state from fresh-start behavior. Preserve the same remaining variables. |
| 10 Gb/s port class | 9+10 separately, at the common speed supported by both adapters | Cover the last two ports after relocating management to a verified unused 2.5 Gb/s port. |

The current controller deliberately prohibits mixing ports 1–8 with 9–10 in a
LAG. Do not bypass that guard to extend the fixed-anchor sweep. An adapter-limited
9+10 run checks LACP at that negotiated speed, not 10 Gb/s throughput.

Use roughly 10 minutes after convergence for a sustained pass, beyond the prior
220–237-second failure window. A port that never synchronizes is a different
failure from a clean interval below the duration threshold. Collect clear failures
without repeatedly disrupting the same state, then choose the discriminating
control. Bracket representative failures with a successful control to detect
harness drift. This phased sweep is not an exhaustive test of every pair and
both bring-up orders; expand the matrix only where the results justify it.

For throughput, provide a verified external packet path and check per-member
counters. A same-host iperf client/server can take a local shortcut unless the
network namespaces and physical return path are deliberately arranged. Two USB
NICs suffice for LACP negotiation; a separate receiver/path is needed to prove
forwarding. Test ordinary single-port data separately from LACP where useful.

## Fresh configuration and interpretation

After preserving the as-is result, a minimal bench configuration following a
factory reset can test for persistent configuration corruption. This is a
separate destructive step for the saved switch setup, to be coordinated after
production has moved and backups are secured; it is not scheduled now. Start
with one Long LAG, the required VLANs, and the independent peer, then add features
back only if that baseline passes.

Reproducing port-dependent failure with the USB/Linux peer would show Firewalla
is not required to trigger it. A Linux pass at 1 Gb/s would leave speed-dependent
behavior, different driver behavior, and Firewalla interoperability open. Failure
after clean startup/minimal configuration would strengthen a QNAP defect case;
it still would not by itself distinguish firmware from hardware damage.
Keep sanitized results in Git and raw captures/backups private. Stop owned jobs
and restore only the dedicated USB interfaces/namespace when the sweep is done.
