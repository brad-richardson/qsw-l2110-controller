# Firewalla engineering review draft — not submitted

## Suggested subject

QNAP QSW-L2110 LACP interoperability: request for peer-side recovery/control review

## Copy/paste report

I have a port-dependent LACP failure involving a QNAP QSW-L2110-10T A0,
QSS 2.2.3.20260713. The same class of failure reproduces with an independent
Ubuntu Linux peer and two Realtek USB NICs, so I am not reporting this as a
confirmed Firewalla-only defect. I would appreciate engineering review of any
supported peer-side workaround or useful diagnostic controls.

With Firewalla at 2.5 Gb/s, QNAP 1+2 and 1+3 sustained over ten minutes of clean
reciprocal LACP and throughput tests. The same Firewalla member/cable on QNAP 4
or 8 failed to synchronize, and returning it to a good port recovered. Physical
carrier stayed up. QNAP advertised Defaulted with zero partner identity while
Firewalla captured outgoing LACP on that member.

The independent Linux bench reproduced failures across most port pairings.
1+7 initially negotiated, then failed a six-minute repeat despite matching
exposed switch settings. Actor identity and configuration-to-peer timing differ
between those runs and remain confounds. Captured frame structure shows no
obvious malformed Linux LACP; per-NIC Ethernet sources differ from the shared
actor system ID, as expected in the inspected upstream Linux implementation.

A real Fast-rate Firewalla test was already performed: QNAP sped up on the working
member but continued roughly 30-second defaulted PDUs on the failing member.
It did not repair negotiation. Cycling the production bond also interrupted
internet forwarding until I reset the LAG; we are therefore doing new controls
on an isolated USB bench and have left Firewalla settings unchanged.

We are preparing tests with fixed actor identity, controlled startup timing,
member/bond interruptions, and switch-side LAG recreation. No recovery mechanism
or peer-side fix has yet been validated.

Could you advise whether your supported bonding controls allow:

- A member-specific recovery without cycling the production LAN bond or losing
  routing/firewall integration?
- Fixed actor identity and controlled active/passive, priority or key settings
  for a reproducible interoperability test?
- Additional evidence to distinguish driver/wire delivery from QNAP failing to
  deliver LACP to the correct member's state machine?

If a bench-only peer change produces repeatable fail/recover/fail behavior, I
can send the exact one-variable procedure, packet sequences, native state and
version information. I am looking for a supported workaround or diagnostic
collaboration rather than assuming a router configuration error.

## Prepared evidence

- [Historical tests](../historical-lacp-patterns-20260906.md)
- [Peer controls and packet comparison](../peer-lacp-controls-20260906.md)
- [Six-minute independent repeat](../linux-usb-1-7-repeat-20260906.md)

No message or attachment has been sent.
