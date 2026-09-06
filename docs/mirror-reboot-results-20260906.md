# Mirror capture and single-LAG reboot results — 2026-09-06

**The failure reproduced after one ordinary reboot with only the production LAG
enabled.** Port-4 LACP became visible on the laptop while the LAG was healthy,
then disappeared from the mirror while Firewalla continued recording outgoing
LACPDUs. QNAP defaulted its partner approximately **93 seconds after the last
mirrored LACPDU**. Port 3 remained clean.

The [prepared experiment](mirror-reboot-preparation-20260905.md) used QSS
**2.2.3.20260713**, group 4 on ports 3+4 with Long timeout, Firewalla bond0 with
eth2 → port 3 and eth3 → port 4, and laptop **en9 → port 2 at 1 Gb/s** as the
ingress-mirror receiver. Downstream branches remained connected. No cables moved,
firmware was uploaded, or factory reset was performed. There was one reboot.

## Capture windows

Times are UTC on September 6; subtract four hours for EDT. Counts use confirmed
mirror-on windows and expected Firewalla actor/QNAP partner identities. Packets
around configuration-request boundaries remain in the raw files but are excluded
from these windows.

| Source | Window, UTC | Duration | Laptop LACPDUs | Firewalla outgoing LACPDUs |
|---|---|---:|---:|---:|
| Port 3, before reboot | 12:14:19.638–12:15:34.684 | 75 s | 2 | 2 |
| Port 4, failed runtime | 12:15:43.306–12:16:58.344 | 75 s | 0 | 5 |
| Port 4, after reboot | 12:18:08.840–12:25:38.988 | 450 s | 4 | 23 |
| Port 3, final control | 12:36:25.393–12:37:40.457 | 75 s | 2 | Separate capture |

Both working-port controls passed. The final control required a separate capture
after a cleanup-session error interrupted the original sequence. It therefore
does not establish continuous operation of the original capture process across
that gap. The original receiver nevertheless continued recording other Slow
Protocol frames after LACP disappeared, including 504 subtype-0x03 frames between
QNAP defaulting and the end of the port-4 window.

All three capture processes—Mac and Firewalla eth2/eth3—reported **zero kernel
drops**, in both the main run and final control. The two initial control frames
and four healthy port-4 mirror frames match decoded Firewalla outgoing frames
one-for-one. Timestamps differ by approximately 4–5 milliseconds between machines.

## Failure timeline

The one reboot request was issued at 12:17:03.372 UTC. Its response was uncertain
and was not retried. Fresh identity and reset uptime confirmed the reboot at
12:17:25.939. Mirroring was re-enabled after full configuration checks; the
receiver mirror misses the early boot interval, which Firewalla's captures cover.

| Event on the port-4 member | UTC |
|---|---|
| First fully clean QNAP actor/partner PDU, states 61/61 | 12:17:47.116 |
| First observed healthy LACPDU at the mirror receiver | 12:18:19.796 |
| Last observed LACPDU at the mirror receiver | 12:19:53.956 |
| Next Firewalla outgoing PDU, absent from the mirror | 12:20:25.304 |
| Another Firewalla outgoing PDU, absent from the mirror | 12:20:56.708 |
| Last clean QNAP PDU before defaulting | 12:21:17.111 |
| First defaulted QNAP PDU, actor 69 with zero partner | 12:21:27.011 |
| Firewalla advertises the resulting 13/69 state | 12:21:27.116 |

The first clean QNAP PDU to defaulting interval was **219.895 seconds**, compared
with approximately 237 seconds in earlier firmware-upload trials. The last
mirrored PDU preceded defaulting by **93.055 seconds**. Port-4 LACP remained absent
from the receiver for the final **345 seconds** of observation. Firewalla recorded
17 outgoing PDUs after defaulting within the remaining mirror window.

Fresh native state and reciprocal router packets were jointly clean for at most
186.842 seconds within the 450-second window, below the five-minute sustained
recovery criterion. Final native states remained **61/61 on eth2** and **13/69
on eth3**.

Native physical-link failure counters stayed unchanged on both members after
initial clean negotiation, with no matching kernel link events in that interval.
During the port-4 mirror window, Firewalla eth3 transmitted about 0.11 Mb/s on
average; the largest sampled one-second rate was about 6.21 Mb/s. These samples
do not support sustained oversubscription of the 1 Gb/s mirror destination, but
cannot exclude short bursts or loss inside the switch.

## Interpretation

The unused second LAG is not required for the observed failure: startup with
one configured LAG reproduced it. The new evidence shows loss of LACP visibility
before the switch declares its partner defaulted. Mirroring stopping only as a
consequence of that defaulted state cannot explain the entire observation.

This narrows investigation to delivery between Firewalla's transmit capture and
the switch's ingress/mirror/LACP processing. Sender-side capture does not prove
wire transmission, and a switch mirror does not expose every internal receive
stage. The evidence does not identify the faulty device conclusively or exclude
a downstream device triggering persistent state. The [earlier branch cuts](port-isolation-results-20260905.md)
tested maintenance of an existing failure; branches were present during this reboot.

## Execution errors and cleanup

The full 450-second observation finished, but the long-lived mirror session's
cleanup POST returned invalid JSON. Fresh authenticated cleanup succeeded.
Session expiry is plausible, but the rejected response body was not preserved,
so the exact cause is unconfirmed. The helper now supports a fresh,
identity-checked session specifically for cleanup.

Stopping already-unloaded transient units also produced a misleading cleanup
error. Direct checks found every owned unit inactive; the retained credential
copy was removed. Retirement now verifies actual unit state and accepts an
already-unloaded unit. The missing 75-second control completed separately with
the corrected cleanup flow, no reported errors, and no additional reboot.

All mirroring is off, the inactive destination selector is restored to 7, both
Mac addressing modes are restored, and owned jobs and temporary credentials are
retired. Supported configuration still matches SHA-256
`095e9b619f7d91c3a3a998906f9d812e538c0342660f0a79c365b0895f433c9c`.
Ports 3, 4, 5, 6, and 10 remain enabled with 2.5 Gb/s carrier; port 2 remains
enabled at 1 Gb/s.

The [sanitized summary](evidence/mirror-reboot-20260906-summary.json) contains
counts, evaluations, timing, traffic/link observations, cleanup results, and
hashes. Private originals are in `backups/mirror-reboot-run-20260906T121056Z/`,
`backups/mirror-final-control-20260906/`, and receiver directories under
`backups/mirror-reboot-prep-20260906/`. No report or capture was sent to a vendor.
