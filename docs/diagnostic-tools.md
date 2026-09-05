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
