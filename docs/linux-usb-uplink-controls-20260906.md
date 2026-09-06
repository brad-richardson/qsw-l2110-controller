# USB uplink and power-cycle startup controls — September 6, 2026

The first three runs are intentionally shortened startup checks, not six-minute
stability tests. A fourth run repeats a connected boot with a full observation.
The operator confirmed port 10 was disconnected throughout the first
two runs and their intervening power cycle, then reconnected it for the third
run without another reboot. The isolated power-off interval was approximately
20 seconds. For the fourth run, the operator confirmed another reboot with
port 10 connected throughout. Physical intervention timestamps were not
independently measured.

All runs use the same actor `02:3f:6c:3a:ff:a7`, the same two Realtek adapters
on QNAP 1+7, and the manual observer with no switch API requests. Group 4 Long
remains operator-declared configuration. No QSS changes were requested between
these runs. All observed member links stayed 1000/full with zero link failures.

| Run start UTC | Operator condition | Last native sample after setup | Observation |
| --- | --- | --- | --- |
| 23:24:29 | Port 10 disconnected; no reboot since preceding failure | 289.188 s | No reciprocal clean interval. Port 1 clean, port 7 defaulted; all ten QNAP port-7 PDUs were actor 69 / partner 0. Linux transmitted 24 PDUs on port 7. |
| 23:30:28 | Power cycle with port 10 disconnected throughout | 77.133 s | Both members reached fresh reciprocal synchronization; 44.739 s of jointly verified clean evidence through the last native sample. Still clean when deliberately stopped. |
| 23:32:05 | Port 10 reconnected; no further reboot | 81.144 s | Linux reached 61/61 on both members, but no jointly verified reciprocal interval. All three QNAP port-7 PDUs advertised actor 61 / partner 69, still reflecting an earlier Linux state after Linux began sending 61/61. |
| 23:33:51 | Another reboot, port 10 connected throughout | 361.133 s (360.016 s observation hold) | Both members initially synchronized; QNAP port 7 defaulted at 189.025 s and did not recover. Port 1 remained clean. Longest joint clean interval: 156.251 s. |

Removing the uplink did not clear the existing failure within the first observed
window. Power cycling while isolated preceded genuine initial recovery. The
short isolated run ended before the earlier 189-second failure point, so it
does not demonstrate that isolation prevents delayed failure. The subsequent
connected run differs in both uplink state and peer restart/time since boot;
it does not establish that reconnecting port 10 caused the stale partner view.
Local TX capture also does not independently prove receipt at the switch.

## Connected reboot reproduces the delayed failure

The fourth run completed its full observation. At **23:37:00.087424 UTC**, QNAP
port 7 changed to actor 69 / partner 0 with zero partner identity. Linux ended
at 13/69 on that member. Both physical links remained up with zero failures,
while port 1 remained synchronized. No switch polling or active diagnostics ran.

The packet timing closely matches the earlier connected no-API observation:

| Connected run | First QNAP port-7 61/61 after setup | First QNAP port-7 default after setup | First 61/61 to default |
| --- | --- | --- | --- |
| 23:03 run | 32.466834 s | 189.062567 s | 156.595733 s |
| 23:33 run | 32.429580 s | 189.025210 s | 156.595630 s |

These are host PCAP timestamps, not switch-internal timer measurements. Their
close agreement supports investigating a repeatable timer/state sequence, but
does not identify which component or event initiates it. In particular, a
defaulted response alone does not identify when incoming LACP stopped reaching
the switch's receive state machine. Two runs are not proof of an invariant timer.

Initial recovery after reboot therefore does **not** require disconnecting port
10, and it has again failed to persist with port 10 connected. The decisive
remaining uplink comparison is a **full-length isolated boot/run**: the short
isolated success ended before either connected run's failure point. Earlier
Firewalla timing used different conditions and should not be pooled as the same
156.596-second interval.

The first three runs were intentionally interrupted with Ctrl-C. Their official tool
results remain `INCOMPLETE / KeyboardInterrupt`; post-hoc analysis of the saved
intervals does not promote them to completed coverage or stable passes. Host
cleanup succeeded each time. Post-hoc checks found complete PCAP records and
zero kernel drops in both captures for each run. The fourth run also passed the
tool's capture-integrity checks, with 361 native samples and a maximum sample
gap of 1.010 seconds. Its cleanup completed at 23:39:52.405335 UTC, returning
both USB interfaces down without errors. No forwarding test was run.

Private roots: `backups/usb-ui-sweep-20260906T232409Z/`,
`backups/usb-ui-sweep-20260906T233017Z/`,
`backups/usb-ui-sweep-20260906T233202Z/`, and
`backups/usb-ui-sweep-20260906T233349Z/`.

[Sanitized evidence](evidence/linux-usb-uplink-controls-20260906.json).
Compare the earlier [six-minute connected run without API polling](linux-usb-1-7-no-api-20260906.md).
