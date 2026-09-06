# Mirror and ordinary reboot preparation — 2026-09-05

**Completed September 6:** see the [results and cleanup record](mirror-reboot-results-20260906.md).
The following preserves the original preparation and its subsequent fixes.
No receiver, coordinator, or cleanup job remains armed. The saved launcher and
capture directories have already been used; see the
[current handoff](diagnostic-handoff-20260906.md) before preparing another run.

The user authorized mirroring and a normal switch
reboot, then deferred execution if this Mac needed a password. Opening BPF as the
current user failed with `PermissionError`; `sudo -n` reported that a password
was required. No mirror source or reboot was enabled during preparation.

On September 6, the first administrator-authorized receiver attempt stopped
before starting tcpdump because the helper incorrectly expected an `IPv4: Off`
line from `networksetup`. The utility does not emit that marker on this Mac.
Both addressing modes were restored and verified. The corrected helper checks
actual interface addresses, permits brief state convergence, and preserves its
raw IP-off readback. That correction passed its regression tests. The original
attempt is preserved privately under `receiver/`; the corrected launch uses
`receiver-2/`.

## Receiver and launcher

Read-only checks confirmed the AX88179B adapter, **en9**, directly on QNAP
**port 2 at 1 Gb/s**, outside any LAG or Mac bond/bridge. The other adapter, en8
on port 1, currently negotiates 100 Mb/s. Wi-Fi en0 is the management default
route. Port 2 belongs to VLAN 3999. All mirror ingress/egress sources are off;
the inactive destination selector is port 7.

The private run directory is `backups/mirror-reboot-prep-20260906/`. It contains
the measured topology, plan, a pinned receiver-helper copy and hash, and
`StartCapture.command`. This launcher starts only the Mac receiver. It does not
start a mirror or reboot the switch.

The original launcher was run in a local Terminal:

```console
backups/mirror-reboot-prep-20260906/StartCapture.command
```

The sudo password stays in that Terminal. Leave the laptop awake with its lid
open and the Terminal running; let the agent know when it prints `Capture ready`.
The helper keeps the Mac from idle system sleep and records for at most 20
minutes, plus cleanup. Ctrl-C or a `STOP` file in the receiver output directory
ends capture early. The launcher refuses to reuse an existing output directory.

[The receiver helper](../tools/mirror_receiver_macos.py) checks the adapter MAC,
service/interface mapping, standalone status, carrier, and independent management
route. It accepts only the currently observed DHCP configuration with a blank
client ID and automatic IPv6. Before opening promiscuous tcpdump, it temporarily
turns off IPv4 and IPv6 on this service. This prevents mirrored IP traffic from
being used as an ordinary IP connection. Capture is limited to EtherType 0x8809,
with 256-byte snapshots and packet-buffered classic Ethernet PCAP.

The helper restores DHCP with an empty client ID and automatic IPv6 after
capture, ordinary exceptions, partial setup failure, Ctrl-C, SIGTERM, or SIGHUP.
It saves restoration status and tcpdump's final counters. SIGKILL or a machine
crash cannot run cleanup. If restoration reports a problem, the recovery commands
for this specifically checked service are:

```console
sudo /usr/sbin/networksetup -setdhcp AX88179B Empty
sudo /usr/sbin/networksetup -setv6automatic AX88179B
/usr/sbin/networksetup -getinfo AX88179B
```

The helper's read-only preflight passed on the actual Mac using stock Python
3.9.6. Fourteen focused tests passed for the new receiver and existing mirror
writer, including restoration after partial configuration failure and ambiguous
switch writes. The corrected receiver subsequently completed privileged capture
and the IP-off/on cycle on this Mac; the results report records the live observations.

## Execution sequence once capture is available

The sequence is now implemented in [the bounded coordinator](../tools/mirror_reboot.py),
with a [separate router cleanup worker](../tools/mirror_cleanup_agent.py).
Runtime recorder/cleanup jobs are installed immediately before the actual test,
after fresh checks. The local coordinator can wait for the receiver; waiting
alone enables no mirror or reboot and installs no remote credentials.

The coordinator sends the observed ordinary-reboot endpoint once. Tests cover a failed working-port
control preventing reboot, an ambiguous accepted reboot response never being
retried, stale or mismatched receiver processes, and cleanup restricted to the
owned ingress sources and destination. A subsequent regression test covers fresh
authentication for cleanup after the observation session expires.

New private plans must include `receiver_mac`, matching the receiver helper's
`--expected-mac` and fresh port-2 MAC-learning evidence. The coordinator no longer
embeds the original laptop's adapter address. Its interface and topology checks
still target the prepared en9/port-2 setup; another machine needs fresh preparation.

1. Acquire the existing diagnostic lock. Take a fresh QSS snapshot and opaque
   backup; verify model/MAC, QSS 2.2.3.20260713, only group 4 on ports 3+4 with
   Long timeout, unchanged VLAN/PVID/port configuration, and the receiver's
   identity and port. Recheck that no existing mirror is active. Start a bounded
   detached Firewalla recorder for eth2, eth3, and native bond0 state.
2. Verify the Mac `ready.json` heartbeat is fresh, its process and tcpdump are
   alive, its PCAP has a valid Ethernet header, and `stopped.json` is absent.
   Have at least 15 minutes remaining. A saved readiness file alone is not
   readiness. Stage an independent bounded mirror cleanup on Firewalla before
   writing the switch, and verify its readiness without enabling a mirror.
3. Mirror **port 3 ingress → port 2** for 75 seconds. Require at least two fresh
   LACPDUs identifying the expected Firewalla actor and QNAP partner port 3 in
   that window. If the positive control fails, stop and clean up; do not reboot.
4. Mirror **port 4 ingress → port 2** for 75 seconds in the existing runtime.
   Record request and readback times and align receiver frames with Firewalla
   native samples and both member captures. Disable mirroring and restore the
   original inactive destination before proceeding.
5. Issue **one normal switch reboot**, using the vendor UI's observed
   `POST /system_reboot.json` with no body. This is not a firmware upload or
   factory reset. Do not retry an ambiguous response. The switch outage also
   interrupts connected LAN/Wi-Fi clients. Both captures must already be running.
6. Reconnect with a fresh authenticated session. Confirm the reboot from uptime
   and observations, then exact device/build and supported configuration. Abort
   on unexplained drift rather than overwriting configuration. This is the first
   startup comparison with only one LAG enabled; the prior firmware trials booted
   with two LAGs. Use a new mirror context after reboot, never the old session.
7. Re-enable **port 4 ingress → port 2** promptly after QSS is available and
   observe for **450 seconds**. Record the boot-to-mirror gap. Do not cycle a
   Firewalla member, disable downstream ports, or make another runtime change in
   this observation. A healthy result after this boot is itself useful evidence.
8. Repeat the **port 3 ingress** control for 75 seconds. Disable all mirror
   sources and restore the prior destination selector, verify readback and full
   configuration, stop/fetch both captures, confirm receiver addressing restored,
   inspect all final drop counters, then retire the owned cleanup/recorder jobs
   and temporary credentials. Preserve PCAPs and UTC windows in the private run.

The existing [mirror context](../tools/mirror_capture.py) supplies ingress-only
payloads and readback/cleanup for each live session. The existing
`RouterRecorder`, snapshot/identity checks, native-state parser, and LACP decoder
can be reused. Do not save the temporary mirror into startup configuration.

## What the result can tell us

A [previous failed-state mirror](lacp-diagnostics-20260905.md) showed zero LACP
frames on port 4 and a positive port-3 control. The new information sought here
is whether port-4 ingress frames are visible while the newly booted LAG is
healthy and what changes as it fails, aligned with the router's observations.

An empty mirror capture cannot prove physical wire-level absence: the ASIC's
mirror stage may depend on forwarding or filtering state. The 1 Gb/s destination
can also lose mirrored packets under enough 2.5 Gb/s source traffic; zero Mac
kernel drops cannot exclude switch-side mirror loss. The early post-boot mirror
gap must remain explicit even though Firewalla captures cover that period.
Judge recovery with reciprocal packets and fresh native state, not merely a
green link indicator. This experiment does not test aggregate throughput or
exclude a downstream device that triggered persistent state earlier.
