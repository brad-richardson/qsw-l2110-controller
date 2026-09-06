# Downstream port isolation results — 2026-09-05

**None of the branch cuts cleared the existing port-4 LACP failure.** Ports 10,
5, and 6 were tested individually, then all three were disabled together. Every
port was restored, the supported configuration matched its baseline, and the
owned workers and recorder were stopped. Temporary router credentials were
removed. The switch remains on QSS 2.2.3.20260713.

The [preparation](port-isolation-preparation-20260905.md) describes the scoped
experiment and independent router-side restoration. Filenames use September 5
in America/New_York; measurements below are September 6 UTC.

## Completed trials

| Disabled ports | Verified disabled interval, UTC | Conservative duration | Fresh native samples | Recovery observed |
|---|---|---:|---:|---|
| 10 | 03:08:41.656–03:09:41.502 | 59.85 s | 59 | None |
| 5 | 03:11:35.749–03:12:35.631 | 59.88 s | 59 | None |
| 6 | 03:14:29.877–03:15:29.735 | 59.86 s | 60 | None |
| 10 + 5 + 6 | 03:21:32.783–03:23:30.964 | 118.18 s | 117 | None |

These conservative intervals begin after all disabled-state checks were written
on Firewalla and end at the first restoration request. They are not exact
physical carrier timestamps. Restoration authentication and verification add
time around the requested 60/120-second deadlines: first disable intent to
enabled verification was approximately 64.8 seconds individually and 126.5
seconds for all three. Each trial included at least 90 seconds of subsequent
observation before another cut or completion.

Throughout every disabled interval, native Firewalla actor/partner states stayed
61/61 on `eth2` → QNAP 3 and 13/69 on `eth3` → QNAP 4. There were no jointly
clean native/packet events during the cuts or the complete trial windows.
Both router captures continued through the outages and ended with **zero kernel
capture drops**. Individual cuts contained 4 captured PDUs on eth2 and 6 on
eth3; the combined cut contained 8 and 12, respectively.

Final readback confirmed ports 3, 4, 5, 6, and 10 enabled with 2.5 Gb/s full-duplex
carrier. The canonical configuration hash was unchanged:
`095e9b619f7d91c3a3a998906f9d812e538c0342660f0a79c365b0895f433c9c`.
This comparison covers exposed LAG, VLAN/PVID, and port configuration; it does
not measure unexposed internal switch state.

## Interpretation

The existing failure persisted without ongoing traffic from any of those three
downstream branches. This substantially narrows a continuous downstream-traffic
explanation for maintaining the failure. It does **not** exclude an earlier
device trigger that left persistent switch or peer state. Firewalla and the
laptop's standalone adapters on ports 1 and 2 remained connected.

The cuts were short recovery screens, not five-minute sustained-recovery or
throughput tests. The subsequent [single-LAG mirror/reboot test](mirror-reboot-results-20260906.md)
also reproduced the failure and observed port-4 LACP disappear from the mirror
before QNAP defaulted its partner. Downstream branches were connected during
that reboot.

## Aborted attempts and harness fixes

The two historical port-10 attempts stopped after about five seconds because the
old script treated changing operational LAG flags as configuration drift. Those
were code bugs, not completed isolation tests. The replacement compares actual
configuration independently from live flags.

A subsequent first run of the replacement also restored port 10 early: it
mistakenly required a JSON body from a successful empty HTTP 200 write response.
The response parser was corrected and regression-tested before the completed
trials above. Its private artifacts remain under
`backups/port-isolation-armed-20260906T025833Z/`.

After the successful individual trials, a duplicate `epoch` argument caused a
final journal-entry error **after** results and restoration had already been
saved. The logging fix was tested and installed before the separately launched
combined trial. The completed trial evidence was preserved; no test needed to
be repeated for that logging-only error.

## Evidence

The [sanitized summary](evidence/port-isolation-20260905-summary.json) contains
timings, state counts, packet/native evaluation, cleanup verification, and raw
artifact hashes. Private evidence remains in
`backups/port-isolation-run-20260906T030616Z/`: snapshots and opaque backups,
final router PCAPs, native samples, phase journals, restoration/readiness records,
and finalized analysis. No evidence was sent to a vendor.
