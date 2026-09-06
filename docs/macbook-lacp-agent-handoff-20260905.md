# MacBook agent handoff: LACP on the existing spare pair

**Historical baseline instructions, superseded September 6.** The tests below
have finished and ports 1+2 are no longer a LAG. Start with the
[current diagnostic handoff](diagnostic-handoff-20260906.md) when resuming work.

Help me use my MacBook and two USB Ethernet adapters as a second LACP peer
for a QNAP QSW-L2110-10T. Run the baseline on switch ports **1 and 2**, in
both connection orders, and save enough evidence to assess sustained
negotiation. Keep my normal internet connection available over Wi-Fi.

The user has just recreated the production Firewalla LAG and explicitly
requested that it remain untouched. Do not change Firewalla settings, cycle
its bond, move its cables, or repeat the Fast LACP experiment. This baseline
needs no switch configuration changes. Leave all existing switch cables in
place, including the production LAN on ports 3+4, and leave port 9 empty
with the ONT connected directly to Firewalla.

Read-only QNAP checks at 20:56 UTC on 2026-09-05 confirmed:

- Ports 1+2: enabled, no carrier, existing LACP group 1, Short timeout,
  untagged VLAN/PVID 3999. These are the two test ports.
- Port 9: no carrier; also VLAN 3999. No DHCP/upstream is expected on this
  isolated VLAN, so a missing lease is expected and is not an LACP failure.
- Ports 3+4: production LACP group 4, VLAN 10, both physical links at 2.5G;
  only port 3 is forwarding. Ports 5, 6, and 10 are also in use.
- Ports 7 and 8 are idle, but they are not configured as this test pair.
  Port 8 is the rescue port. Do not substitute either for ports 1 or 2.

1. **Update the repository.** Use a checkout of
   `https://github.com/brad-richardson/qsw-l2110-controller.git` and update
   `main` with `git pull --ff-only`, preserving any existing local work.
   Read this handoff and `docs/lacp-review-20260905.md`. The harness is
   `tools/lacp_peer_test.py`; it is a standalone Python file with no
   third-party Python dependencies. Run the commands below from the
   repository root. No switch or Firewalla credentials are needed for
   this baseline.

2. **Identify and check the adapters before connecting Ethernet.** Both
   adapters may be plugged into USB now. Save `sw_vers`, hardware/USB adapter
   identities, current network services, existing bonds, interface MACs,
   and the default route. Useful discovery commands are:

   ```sh
   networksetup -listallhardwareports
   networksetup -listallnetworkservices
   networksetup -listBonds
   route -n get default
   ifconfig -a
   ```

   Identify the two actual USB Ethernet `enX` devices; do not assume names
   or use Wi-Fi, a working uplink, or an existing bond member. Check
   `networksetup -isBondSupported enX` separately for each, using the local
   `man networksetup` for the installed macOS version. If either is
   unsupported or the command is unavailable, report the exact output and
   resolve native support before proceeding. An unsupported adapter is
   not evidence against the switch. Confirm an available Python 3.9+
   interpreter and `tcpdump`; use the same interpreter under sudo.

3. **Create only a temporary Mac bond.** Confirm neither test adapter
   participates in a bridge or Internet Sharing. Preserve Wi-Fi, service
   order, existing bonds, and routes. Save the two adapters' existing
   configuration so it can be restored. With both Ethernet cables still
   disconnected, use the harness, substituting the discovered interfaces:

   ```sh
   sudo python3 tools/lacp_peer_test.py setup --bond LacpUsbTest --member enX --member enY
   networksetup -listBonds
   ```

   Choose a different test name if it already exists. Determine the actual
   new `bondN` device from native output; never assume `bond0`. Verify its
   two members and LACP mode with `networksetup -showBondStatus bondN` and
   `ifconfig -v bondN`. The harness's `--lacp-rate` option applies only to
   Linux; it does not set a Mac bond's rate. Do not assign a LAN IP or
   gateway to this isolated bond. Continue only while Wi-Fi internet
   access remains available. Keep the Mac awake during recording.

4. **Run the baseline twice.** Label adapter/cable A as switch port 1 and
   B as port 2; retain this mapping across both runs. Start a fresh capture
   with both Ethernet cables unplugged, verify both tcpdump processes
   remain running without errors, then tell me exactly which cable to
   plug. A command template, from the repository root, is:

   ```sh
   sudo python3 tools/lacp_peer_test.py record --bond bondN --member enX --member enY --duration 900 --output "$HOME/lacp-tests/ports-1-2-port1-first"
   ```

   Use a new output directory; the recorder refuses existing directories.
   Run A: ask me to plug A into port 1. After native/packet evidence shows
   that member joined, or after 180 seconds if it has not, ask me to plug
   B into port 2. Record at least five minutes with both connected. Require
   five minutes of sustained two-member synchronization to call it a pass;
   extend the recording within the 15-minute limit if necessary. If it
   never joins, five minutes with both connected still yields a useful
   failure recording. Mark each cable event with a UTC timestamp:

   ```sh
   sudo python3 tools/lacp_peer_test.py mark --output "$HOME/lacp-tests/ports-1-2-port1-first" 'A/enX connected to switch port 1'
   ```

   Finish the capture cleanly with SIGINT after the observation window,
   while both cables are still connected, so it saves its final summary.
   Then unplug both test cables, confirm carrier is down, and wait 120
   seconds. That wait is an aging interval, not proof of a firmware reset.
   Run B: start a new capture in `ports-1-2-port2-first`, connect B/port 2
   first and A/port 1 second, and repeat the same timing. Do not reboot or
   reconfigure the switch between runs. Monitor Wi-Fi internet access.

5. **Assess packets and native state, not just the harness label.** The
   current harness caches the latest PDU and can keep printing `SYNCED`
   or `synced_at_end: true` after packets stop. Check ongoing packet
   timestamps and cadence in each direction, native member status,
   carrier, and tcpdump errors/drops. Check actor/partner system IDs,
   keys, and port IDs match reciprocally on each link. Verify how the
   actual Mac's packet sources are classified; the harness's MAC heuristic
   is not authoritative. Both peers should advertise synchronization,
   collecting, and distributing on both members, without defaulted or
   expired state. Do not require one particular decimal state value.
   Record negotiated speed/full duplex on each member; unequal speeds or
   missing capture visibility make the result inconclusive. A brief sync
   followed by failure matters: earlier switch reboot tests had about ten
   seconds of apparent success before the second member failed.

6. **Preserve evidence and clean up.** Save both original PCAPs, tcpdump
   logs/drop counters, native bond/member snapshots, adapter and speed
   details, event marks, summaries, and a short report. State whether both
   members sustained LACP, whether connection order changed the result,
   when any state changed, and whether Wi-Fi internet remained available.
   Do not treat a 1G Mac success as proof Firewalla is faulty; speed,
   driver, and peer differ from the 2.5G production test. This isolated
   baseline tests negotiation, not routed traffic or throughput. Unplug
   the two test cables before removing only the Mac bond created here:

   ```sh
   sudo python3 tools/lacp_peer_test.py teardown --bond bondN
   ```

   Restore the test adapters' prior configuration as needed, stop only
   capture/keep-awake jobs created for this test, and verify the original
   Wi-Fi route and internet access. Report results before proceeding to
   another port pair. The next proposed comparisons are group 1 on 1+7,
   then 2+7; they require separate switch planning and read-back. Do not
   apply an example YAML or move production cables as part of this baseline.

The older laptop runbook contains superseded conclusions and a missing
ports-7+8 YAML reference. Follow this handoff and the later review. The Fast
Firewalla trial did not fix port 4; cycling that managed bond lost internet
access even after its rate and administrative state were restored. The
user's subsequent LAG recreation was followed by successful internet probes.

Apple documents native macOS LACP in
[Combine Ethernet ports into a virtual port](https://support.apple.com/guide/mac-help/combine-ethernet-ports-a-virtual-port-mac-mchlp2798/mac).
Use the installed Mac's native tools to establish whether these particular
adapters support it.
