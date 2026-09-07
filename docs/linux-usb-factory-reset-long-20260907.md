# Factory-reset USB 1+7 / Long observation — September 7, 2026

## Result: six-minute negotiation observation passed

The operator reported a factory reset, followed only by a password update,
configuration of LACP group 4, and changing the default Short timeout to Long.
The manual observer ran on the same two Realtek USB NICs on QNAP 1+7, using
the same actor identity `02:3f:6c:3a:ff:a7` as the preceding failed controls.
The tool and agent made no switch API requests or configuration changes during
observation. Port-10 connection status has not yet been confirmed for this run.
Factory reset and configuration are operator-reported, not API-verified.

Run `usb-ui-sweep-20260907T000941Z` started peer setup at 00:10:41.587959 UTC
and completed a **360.025-second observation hold**. Both members ended at
Linux/QNAP 61/61, with **329.773 current continuous seconds** of jointly
verified native state and fresh reciprocal LACP. The run met the observer's
300-second stability criterion. Port mapping was confirmed by QNAP-origin PDUs.

Both links remained 1000/full with zero member link failures and zero final
partner-churn counts. There were 361 native samples with maximum gap 1.011
seconds. Both captures contained 26 decoded LACP packets, were complete, and
reported zero kernel drops. Host cleanup finished at 00:16:42.894364 UTC
without errors, returning both USB NICs down.

This is a negotiation/stability observation, not a throughput test, per-member
forwarding validation, or proof of long-term reliability.

## Meaning and next timeout comparison

The two earlier full connected/no-API runs lost port 7 approximately 189 seconds
after setup; this run remained healthy beyond that point through completion.
It demonstrates that **group 4 / Long on 1+7 is capable of sustained negotiation
in this reset configuration**. Factory reset was followed by improvement, but
which settings or internal state changed has not been established. Uplink state
also remains unconfirmed. Do not assign the earlier failure solely to Long,
group number, or a permanently unsupported port pairing.

QNAP Short has **not yet been tested in this exact factory-reset Linux USB
configuration**. Earlier Firewalla Fast did eventually apply despite an initial
EBUSY failure, and it did not fix the failing member. The earlier Mac
Short→Long→Short test had a separate timeout-dependent failure. Neither replaces
the proposed USB comparison. See [peer-side controls](peer-lacp-controls-20260906.md).

The subsequent **3+4 / group 4 Long** test also passed its full observation,
with 329.777 continuous clean seconds. The operator then requested an
authenticated configuration comparison. See the [3+4 result and readback](post-reset-config-comparison-20260907.md).

For a separate Short trial, manually change only group 4's member timeout to
Short in QSS, keep the same pair/actor and other conditions, then run:

```bash
sudo .venv/bin/python -m tools.lacp_ui_sweep --bench-isolated \
  --ports 1,7 --group 4 --actor 02:3f:6c:3a:ff:a7 --switch-timeout short
```

The flag declares the expected QNAP timeout for packet validation; it does not
configure QSS. Linux continues to request Slow, so this changes the QNAP timeout
advertisement while preserving the Linux request. Inspect actual cadence and
timeout bits in both directions. A later return to Long can check repeatability;
applying LAG settings may also refresh internal state, so a one-off improvement
would not by itself prove a timeout-only cause.

[Sanitized evidence](evidence/linux-usb-factory-reset-long-20260907.json).
Compare the [uplink/reboot controls](linux-usb-uplink-controls-20260906.md).
