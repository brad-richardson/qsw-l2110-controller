# Fixed-identity USB LACP recovery tests — September 6, 2026

The runner targets the existing isolated QNAP **1+7, group 4 Long** bench and
keeps Linux Slow with actor **02:3f:6c:3a:ff:a7**, matching the failed six-minute
repeat. It is a new experiment tool, not a demonstrated recovery workaround.
It does not change Firewalla, VLANs, startup configuration, or ports 9–10.

The preceding preparation did not explicitly issue Save, but the 22:53 UTC
read after power-up still found 1+7 group 4 Long. Persistence semantics remain
unverified. Re-read identity, configuration, cabling, and management reachability
after any power cycle before using these commands. Neither mode has been run
on hardware.

## Staged recovery

Keep both test cables on 1+7, management on 10, and the USB NICs initially down
as left by the preceding control. From the repository root:

```bash
sudo .venv/bin/python -m tools.lacp_recovery \
  --bench-isolated --insecure --recreate-lag
```

The explicit `--recreate-lag` allows the final QNAP membership reset. Without it,
only the two host-side recovery actions are tested.

The sequence is:

1. Observe the baseline. If it is already stably healthy, finish without an action.
2. Pulse administrative state on the USB member identified as QNAP port 7.
   This does not assume that the switch's physical carrier drops.
3. If still failed, recreate the Linux bond with the **same actor identity**.
4. With `--recreate-lag`, remove/re-add only the same isolated switch LAG while
   both USB interfaces are down, then immediately rebuild the same peer.

Negative phases last 90 seconds. A phase that reaches reciprocal synchronization
can extend to at most 360 seconds, requiring **300 current continuous seconds**
of native plus fresh reciprocal evidence to pass. Historical clean peaks do not
count as current recovery. The overall default deadline is 25 minutes. Expected
runtime is much shorter when all phases fail their 90-second windows.

The runner stops after the first sustained healthy phase and records which
intervention preceded it. Even a pass requires a later reproducibility test and
traffic validation before proposing a production watchdog or Firewalla workaround.
This version does not perform a true switch-port admin pulse or a switch reboot.
Those are separate experiments if host/member and LAG resets do not discriminate.

## Observe without switch polling; optional uplink isolation

```bash
sudo .venv/bin/python -m tools.lacp_recovery \
  --bench-isolated --insecure --offline-observe
```

This first verifies the existing switch configuration while port 10 is still
connected, then waits at READY for Enter **before starting the Linux peer**.
For the first control, leave port 10 connected and press Enter. For a separate
isolation run, unplug only the QNAP port-10 uplink at READY, then press Enter.
Keep the host's eno1 connection to its normal network intact and retain 1+7.

After that gate, the tool performs native/PCAP observation without switch API
requests or recovery actions. A negative run observes 360 seconds; a sustained
healthy run may finish after its 300-second clean-evidence criterion. It cleans
up the host without querying or changing the disconnected switch. Reconnect
port 10 after DONE if it was disconnected. Final switch configuration cannot be
reverified while offline; the log explicitly records that limitation.

Use this as an attached-versus-disconnected control with identical actor, cables
and startup timing. It is **not** a cold-switch-start test: previously established
internal switch state can remain. A later isolated reboot needs deliberate saved
configuration preparation and readback; do not infer startup behavior merely
from whether the preceding script explicitly issued Save.

Do not unplug port 10 during ordinary recovery mode or the older sweep runner,
which need their switch API connection. Offline observation cannot be combined
with `--recreate-lag`.

## Evidence and cleanup

Each run creates a private `backups/usb-recovery-<UTC>/` directory containing
settings and identity, initial switch/host state, per-member PCAPs and capture
logs, native bond samples, Linux counters, phase/action/transition events,
phase summaries, and cleanup records. Normal recovery mode also records switch
samples and verifies/restores the initial tracked LAG configuration on exit.
Unrelated configuration drift is not overwritten. USB host cleanup runs even
if switch restoration cannot be verified.
If a LAG write reaches the switch but its verification read fails, the runner
can refuse restoration because the resulting configuration is uncertain. It
reports that failure instead of overwriting an unverified state; inspect the
switch before another run. An unsaved LAG may be left removed in this case.

The terminal shows preparation, actions, holds and results. Artifacts are returned
to the invoking sudo user for live review. Ctrl-C/SIGTERM invokes cleanup; a hard
kill or physical USB removal can prevent completion. Inspect `host-cleanup.json`
and `recovery-cleanup.json` before another attempt. Runs do not resume halfway
through a perturbation sequence; start a fresh logged sequence after cleanup.

See [remaining controls and current switch settings](lacp-next-controls-20260906.md),
[peer-control analysis](peer-lacp-controls-20260906.md), and the
[failed six-minute repeat](linux-usb-1-7-repeat-20260906.md).
