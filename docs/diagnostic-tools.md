# Reusable diagnostics

Run these tools from the repository checkout after `uv sync --locked --extra dev`.
They are optional research helpers, separate from the installed controller CLI.
Passwords come from `QSW_PASSWORD` or an interactive prompt. Use a new output
directory for each capture; directories are mode 0700 and files mode 0600.
Keep captures under ignored `backups/`: they can contain device identities,
configuration, session-bearing DOM, and vendor assets. Review and sanitize
anything intended for an issue or public repository.

## Capture the QSS interface

Install Node Playwright separately; for example, `npm install --prefix /tmp/qss-browser playwright`.
Use an existing Chromium/Chrome executable or install Playwright's Chromium.
Then run:

```console
uv run python -m tools.capture_qss_ui --host https://switch.example \
  --output backups/ui-capture \
  --playwright-module /tmp/qss-browser/node_modules/playwright \
  --browser-executable /usr/bin/google-chrome
```

`--insecure` explicitly permits the switch's self-signed certificate in the
Python client and browser. `--page link_aggregation.html` selects one reviewed
page; repeat it for multiple pages. By default, all reviewed pages are visited.

The browser saves response bodies and static assets, plus a screenshot, DOM,
native controls, and successful form entries for each page. Form entries remain
an array so duplicate names are preserved. Authentication happens outside the
browser recorder; session cookies travel to Node over stdin. Request manifests
omit headers, query strings, and cookies. These measures do not make the raw
artifacts public-safe.

The request guard allows only reviewed same-origin GET handlers and static
assets. It blocks all browser writes, authorization/logout requests, unreviewed
handlers, and external destinations. QSS requests are serialized because this
firmware can fail under concurrent browser loads. No HAR is produced.

Inspect `browser-manifest.json` for blocked/failed requests and page errors.
`shellLoaded` means only that the page shell appeared; hidden pages may contain
broken or partially populated controls. A screenshot alone does not prove
that every field loaded correctly. See the [hardware UI audit](qnap-ui-audit-20260905.md).

## Capture LACP over SSH

```console
uv run python -m tools.capture_lacp --ssh-target user@router.example \
  --identity-file ~/.ssh/router_capture --interface eth2 --interface eth3 \
  --duration 40 --output backups/router-lacp
```

The remote Linux host needs `sudo -n`, `timeout`, and `tcpdump`, and existing
SSH host-key trust. The tool does not alter bonds or switch settings. It captures
only untagged LACP (EtherType 0x8809, subtype 1), with a 256-byte snapshot and
1–300-second duration. Interfaces are captured concurrently. `--direction out`
or `in` narrows direction. Promiscuous mode is off unless `--promiscuous` is given;
enable it on a dedicated mirror receiver.

PCAPs, tcpdump diagnostics, and a timestamped manifest are retained even when
SSH or capture fails. A valid PCAP header with a normal exit or timeout is
reported as capture success; this does not imply that any packets were received.
Sender-side capture establishes what the kernel handed to the interface, not
what the switch physically received.

For a compact state summary without tcpdump or additional Python dependencies:

```console
uv run python -m tools.summarize_lacp backups/router-lacp/eth2.pcap
```

The summary groups Ethernet sources and LACP actor/partner identities, keys,
ports, and state bytes, with packet counts and first/last UTC timestamps.
It accepts classic Ethernet PCAP, including up to two VLAN headers, and rejects
truncated records. It does not accept pcapng or assess full standards compliance.

## Temporary ingress mirror

Connect a dedicated capture NIC directly to an unused non-LAG switch port.
A 1G laptop adapter suffices for LACP. Avoid an intermediate switch or a live
bond member as the receiver. Keep a separate management path available.
Start a promiscuous capture first. On macOS, find the adapter with
`networksetup -listallhardwareports`, then substitute its `en` device:

```console
umask 077
sudo tcpdump -i en8 -nn -s 256 -U -w ~/Desktop/qnap-ingress.pcap 'ether proto 0x8809 and ether[14] = 1'
```

From the controller checkout, enable a bounded mirror window:

```console
uv run python -m tools.mirror_capture --host https://switch.example \
  --source 4 --destination 7 --duration 45 --dedicated-receiver \
  --output backups/ingress-port4
```

This experimental writer is limited to QSW-L2110-10T QSS 2.2.3.20260713.
It backs up configuration, refuses an existing active mirror or a LAG-member
destination, uses the native UI's two-request payload, verifies ingress-only
read-back, and disables all mirror sources on exit. It never saves configuration.
An originally unset destination selector may remain selected after cleanup;
all ingress and egress sources must be disabled. SIGTERM and ordinary failures
attempt cleanup; process kill or lost switch access can prevent it. The QSS
Port Mirroring page can disable all source checkboxes manually.

Repeat with a known working source port as a control, and retain the printed
UTC windows. Stop tcpdump with Ctrl-C after the mirror is disabled. Decode with:

```console
tcpdump -nn -e -vvv -r ~/Desktop/qnap-ingress.pcap
```

The ASIC may not mirror reserved link-local control frames at the stage expected.
An empty capture is therefore inconclusive without a working-port control.
Even a positive mirror capture establishes packet availability at the mirror
stage, not acceptance by the switch's LACP state machine.

## Dynamic MAC pagination

`dump-mac-table` now follows `next_offset` until `has_more` is false. This
firmware may return only eight entries on the first page. The client rejects
malformed or nonadvancing pagination instead of returning a silently partial
table. Learning and aging continue during the reads, so the combined result
is not an atomic snapshot.

## Detached recorder on the router

`tools/capture_lacp.py` streams over SSH, so a LAN outage during an experiment
ends the recording. The recorder instead runs on the router itself under a
transient `systemd-run` unit (or `nohup` without systemd) and writes to the
router's filesystem. Start it before the change, make the change, then fetch:

```console
uv run python -m tools.firewalla_recorder start --ssh-target pi@router.example \
  --identity-file ~/.ssh/router_capture --interface eth2 --interface eth3 \
  --bond bond0 --duration 900 --label lan-order --output backups/rec-lan-order
uv run python -m tools.firewalla_recorder status --output backups/rec-lan-order
uv run python -m tools.firewalla_recorder fetch --output backups/rec-lan-order
```

Each run records, at one-second intervals by default and all in UTC, the full
`/proc/net/bonding/<bond>` text (aggregator IDs, churn states and counts, actor
and partner LACPDU details), `ip -s -s link show` for every interface, kernel
link events from `journalctl -k -f`, and a per-interface tcpdump limited to
EtherType 0x8809 unless `--capture-filter all` or another filter is given.
The run directory defaults to `/home/pi/lag-recorder/<label>-<timestamp>`.
`stop` ends a run early. `fetch` copies the directory into `<output>/remote/`
with private permissions and checks every PCAP header. Nothing managed by the
router vendor is modified; the transient unit is removed when the run ends.

For a timed trial, treat the second member's link-up in `kernel-events.txt` as
T0 and do not roll back sooner than three minutes after it. Compare the switch's
per-port good-packet deltas against `link-stats.txt` over the same window to
establish loss-free delivery in both directions.

## ARP ingress probe from an inactive bond slave

```console
uv run python -m tools.arp_ingress_probe --ssh-target pi@router.example \
  --identity-file ~/.ssh/router_capture --interface eth2 --control-interface eth3 \
  --target-ip 192.0.2.1 --output backups/arp-probe
```

The probe sends three ARP requests for the switch's management address from a
raw socket on the slave under test, using a locally administered test MAC and a
0.0.0.0 sender address, while capturing on every slave. Because the raw socket
bypasses Linux bonding, the frames leave an inactive slave. A reply on the other
slave means the switch attributes that port's ingress to the LAG and answers via
the active member; check `dump-mac-table` for which port learned the test MAC.
No reply while the control probe gets one means the port's ingress is discarded.
No switch or router configuration is changed; the remote temporary directory is
removed after the captures are copied.

Result on 2026-09-05 (private evidence in `backups/arp-ingress-probe-20260905T155702Z/`):
probes sent from failing `eth2` into QNAP port 4 were answered within 0.4 ms, and every
reply arrived on `eth3` via port 3. The switch learned the test MAC, and `eth2`'s
permanent MAC that only ever appears as its LACPDU source, on port 3. The switch
therefore already treats the unsynchronized member's ingress as LAG traffic mapped
to the lowest-numbered member, which is consistent with its LACP receive machine
never seeing that member's own LACPDUs.

## Runbooks

- [LAN LAG on ports 1+2, both bring-up orders](runbook-lan-lag-ports-1-2.md) with its YAML
  under `examples/experiments/`.

- [Second LACP peer from a laptop](runbook-laptop-lacp-peer.md) with `tools/lacp_peer_test.py`,
  a single-file macOS/Linux harness that reads sync state from captured LACPDUs.
