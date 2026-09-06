# Manual QSS configuration, automated Linux LACP observation

This mode **never opens a QNAP session**: no authentication, API polling,
configuration read/write, backup, or save. The single QSS session stays available
to the operator. Switch IP/password changes after a factory reset do not affect
the recorder. Firewalla is not accessed.

The operator resets and configures the switch entirely through its native UI.
The tool prepares an isolated, unaddressed Linux USB bond and checks LACP for
**six full minutes per pair**, including when negotiation succeeds immediately.
Default coverage is all six pairs among ports 1–4:
**1+2 → 1+3 → 1+4 → 2+4 → 2+3 → 3+4**. Ports 9/10 never become bond members.

## Start

Keep the host's normal `eno1` management connection intact. Both dedicated USB
NICs must be administratively down, unaddressed and unenslaved before launch;
the preceding tools leave them this way. Their Ethernet cables may already be
on QNAP 1+2. The unused ASIX adapter is excluded.

After the factory reset, use QSS to configure **LACP group 1, Long timeout,
ports 1+2**, with matching default untagged VLAN/PVID settings. Remove old members
when changing pairs so only the requested pair belongs to the group. Keep other
settings consistent across the experiment and keep unrelated devices off test
ports. The program cannot verify these UI settings without accessing the switch.

```bash
cd /home/brad/dev/qsw-l2110-controller
sudo .venv/bin/python -m tools.lacp_ui_sweep --bench-isolated
```

For every READY prompt:

1. Apply the indicated pair in QSS, using group 1 and Long timeout each time.
2. Connect `enx5c857e38d8d1` to the **lower-numbered** port and
   `enxa0cec8597422` to the **higher-numbered** port. The prompt repeats the
   exact mapping. This keeps actor member IDs consistent rather than minimizing
   cable moves at the expense of changing member assignment.
3. Press Enter after applying and cabling. Leave QSS settings and cables still
   during HOLD. An ESTABLISHED message is an interim observation, not permission
   to advance. The tool continues all 360 seconds to catch delayed failures.
4. Wait for RESULT and the next READY before changing anything. Host cleanup
   finishes before the next prompt; USB interfaces are returned down each time.

At READY, type another pair, such as `1+2`, to select or repeat it; a second
prompt requires Enter before it starts. `q` quits between trials. Ctrl-C during
a trial records an incomplete attempt and performs host cleanup. All six
recorded pairs finish automatically. Allow 36 minutes of observation plus UI
and cable changes. There is no wall-clock limit while waiting for the operator.

The actor MAC is generated once, printed and saved, then retained across every
fresh peer and any resume. `--actor 02:...` can explicitly fix it for a separate
controlled run. Linux requests Slow LACP. `--switch-timeout short` changes the
operator-declared QSS timeout and packet check for a separate experiment; it
does not configure the switch. The default switch system MAC is the existing
bench's `24:5e:be:77:e5:86`; a different unit requires `--switch-mac`.

## Results and limitations

- `NEGOTIATED`: currently clean native state plus fresh, reciprocal LACP evidence
  for at least two seconds at the end. A separate STABLE line requires at least
  **300 current continuous seconds**; an earlier healthy peak cannot satisfy it.
- `NEGOTIATED_THEN_LOST_OR_INCOMPLETE`: reciprocal evidence existed but did not
  remain confirmed at the end. Inspect the transitions and current/longest times.
- `NOT_ESTABLISHED_IN_WINDOW`: both expected QNAP ports sent LACP, but the
  required two-member negotiation was not confirmed during the observation.
- `NO_PEER_LACP_IN_WINDOW`: at least one member has no captured QNAP LACP.
  **Port mapping is unverified** where packets are absent; the requested pair
  comes from the operator. This is not proof of a specific physical port defect.
- `NATIVE_ONLY_NEEDS_MORE_TIME`: native state looked clean, but reciprocal packet
  evidence was insufficient. It is not a confirmed pass.
- `INCOMPLETE*`: wrong advertised port/switch/timeout, missing Linux TX, unusable
  USB speed/duplex, carrier loss, failure-counter changes, interruption, capture
  loss, or cleanup trouble. These attempts do not complete pair coverage.

Unexpected identity/mapping is recorded rather than silently relabeling a test.
The port check uses switch-origin LACP actor fields, not a switch API or a stale
native partner entry. It cannot independently validate hardware port labeling.
The tool detects brief carrier flaps through native failure counters as well as
sampled carrier state. If a trial is invalid it prompts to retry; cleanup errors
stop the run. Results cover negotiation/stability only, not data forwarding.

The confirmation timestamp is recorded, but **the actual QSS Apply timestamp is
unknown**. This manual run does not isolate subsecond apply-to-peer startup timing.
Firmware, group, VLAN, factory-reset completion and outside-port configuration
remain operator-provided context; packet fields cannot verify all of them.

## Artifacts and resume

Each run has a private `backups/usb-ui-sweep-<UTC>/` directory with atomic
`summary.json`, `summary.csv`, `coverage.json`, settings, actor, host identity,
instructions and operator-ready events. Each attempt has its own `trial-*`
directory containing per-member PCAPs/tcpdump logs, native bond snapshots,
Linux counters/link events, transition log, result, capture-integrity checks and
`host-cleanup.json`. Final classification requires complete PCAPs with zero
kernel drops and successful capture shutdown. Artifacts are returned to the
invoking sudo user for live inspection; raw files remain out of Git.

After an interrupted run, wait for cleanup and use its printed directory:

```bash
sudo .venv/bin/python -m tools.lacp_ui_sweep --bench-isolated \
  --resume backups/usb-ui-sweep-YYYYMMDDTHHMMSSZ
```

Resume creates a new directory, carries forward measured results and their
original paths, retains actor identity, verifies the same adapters/drivers and
test settings, and retries incomplete pairs. Manually configure the next pair
shown at READY. Use a fresh run after another factory reset or a changed test
condition; resume preserves progress, not experimental equivalence across resets.

No switch cleanup or restoration is attempted, including on error. A hard kill
or USB removal can prevent host cleanup; inspect the last trial's cleanup before
starting again. The independent host management route is checked using local
`ip route get 1.1.1.1`; this sends no ping, HTTP request, or other probe traffic.
