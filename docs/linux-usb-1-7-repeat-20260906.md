# Linux USB 1+7 repeat — September 6, 2026

Later follow-up: the [23:04 observation without switch API polling](linux-usb-1-7-no-api-20260906.md)
used this repeat's actor identity after a switch power cycle, synchronized
briefly, then lost port 7 after 189 seconds. It did not sustain recovery.

## Result: no two-member synchronization in six minutes

Run `usb-sweep-20260906T192611Z` measured **360.030 seconds** on QNAP 1+7.
All **430 native samples** were negative for a clean two-member bond; longest
joint native/reciprocal clean interval was zero. Port 1 reached 61/61, while
port 7 ended at Linux actor 13 / QNAP actor 69. Both links stayed at 1000/full
with zero member link failures. Port 7 recorded one partner-churn event.

The QNAP emitted 12 LACP messages on port 7 during the window, all defaulted
with zero partner identity. The USB capture showed Linux transmitting LACP on
that member, including 23 frames advertising actor state 13 and QNAP partner
state 69. Local TX capture proves the host handed frames to its capture path;
it does not independently prove arrival at the switch ingress/CPU.

Both capture processes exited successfully with zero kernel drops. They contain
29 and 40 decoded LACP frames overall (28 and 40 inside the measurement window).
The maximum native-snapshot gap was 1.662 seconds. This is a complete negative
negotiation test, not a throughput test.

## Comparison with the earlier success

The [full sweep](linux-usb-sweep-results-20260906.md) recorded genuine reciprocal
1+7 synchronization in trial 6: both directions on each member advertised 61/61
and the correct physical partner ports. Native synchronization remained until
approximately 96 seconds after trial start. That earlier result is retained;
the repeat establishes that it is not reliably reproducible in the tested sequence.

The repeat used the same RTL8153/r8152 NICs, same NIC-to-port mapping, 1000/full,
group 4 Long, Linux Slow, and VLAN 1. Saved configuration comparisons matched
LAG settings, VLAN/PVID, port settings, and mirroring exactly.

Two material differences remain:

- Fresh Linux actor identity: `02:3f:6c:3a:ff:a7`, compared with
  `02:17:fa:21:54:71` during the sweep. A new process generates a new actor.
- Preparation timing/history: switch configuration verification preceded the
  repeat peer rebuild by approximately 116 seconds, versus approximately
  0.092 seconds in the successful sweep trial. The full intervening sweep also
  changed the switch's history. These differences prevent attributing the change
  solely to port pairing or to peer identity.

The sweep's successful 1+7 occurred before its first 2+3 test, immediately after
failed 1+6. Thus a prior successful **2+3 pair** was not required by that observed
sequence. A weaker idea—that earlier success of the same peer member on port 2
or 3 affects later behavior—remains untested.

**Do not treat 1+7 plus 2+3 as a validated simultaneous-LAG solution.** The
pairs were only tested separately, and 1+7 has now failed a six-minute repeat.
A controlled repeat should hold actor identity and config-to-peer timing fixed
before testing a good-port priming sequence or two simultaneous groups.

## Preparation and final state

At the user's request, a guarded switch-only preparation configured 1+7 in
group 4 Long while both USB host interfaces were down and unaddressed. It verified
the previously neutral bench state and links only on 1, 7, and management 10.
It preserved VLANs and ports 9–10 and did not save the change to startup.
Private preparation artifacts are in
`backups/usb-1-7-repeat-prep-20260906T192356Z/`.

The user launched the existing-pair control with `--seconds 360`. That capture
performed **no switch writes**. At **19:32:27 UTC**, host cleanup verified both
USB interfaces returned down with original MACs and no errors. The QNAP remains
configured for **1+7 group 4 Long**; this existing-pair control did not neutralize
or save switch configuration. Firewalla configuration was not changed.

[Sanitized evidence](evidence/linux-usb-1-7-repeat-20260906.json).

Follow-up research: [historical ordering and controlled tests](historical-lacp-patterns-20260906.md), [firmware and public-code findings](public-lacp-code-20260906.md).
