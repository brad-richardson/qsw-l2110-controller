# Remaining peer, recovery, and isolation controls — September 6, 2026

## Assessment

There are useful controls left on the Linux peer and on the switch. Current
evidence favors a QNAP-side interoperability/state issue, but does not prove a
root cause or identify a supported Firewalla workaround. The independent peer
makes it possible to develop a reproducible fail/recover sequence without
cycling the production router's LAN bond.

## Fresh read-only switch audit

At 19:45:59 UTC, the known QSS GET endpoints reported:

| Control | Current state | Interpretation |
| --- | --- | --- |
| Loop detection/prevention | On; zero violations on all ports | Historically tested off across reboot without sustained repair; not a new untested toggle |
| STP | Disabled | Not currently blocking a member according to the exposed setting |
| EEE | Disabled/inactive on all ports | Already removed as an ordinary energy-saving setting |
| Storm controls | All categories zero/off on all ports | No configured broadcast/multicast/unicast limiter found |
| Ingress/egress rate limits | Zero/off on all ports | No configured port rate cap |
| IGMP snooping | Off | Child fast-leave/report-flood values remain stored but snooping is off |
| DHCP snooping | Off | No active configured snooping policy |
| ACLs | Zero entries | No listed ACL filter |
| Flow control | On, including 1/7/10 | A lower-priority one-variable bench control remains available |
| Test port speed | Auto, actual 1000/full on 1 and 7 | Matches successful USB control |

The [sanitized audit](evidence/qss-settings-audit-20260906.json) records exact
responses. These are exposed configuration values, not proof of internal state.
The [earlier rendered-UI audit](qnap-ui-audit-20260905.md) also covered hidden
pages, frame admission, mirror controls and native form serialization. The
controller payload differs from native FormData in disabled-port fields; later
native-UI group operations did not provide a sustained cure, but a future minimal
vendor reproduction should still prefer a freshly reviewed/native configuration.
A factory-default minimal configuration has not been tested as a controlled
variable. Do not conflate rebooting or reinstalling firmware with factory reset.

## Peer and recovery controls, in priority order

1. Fix actor identity, member IDs/order and configuration-to-peer startup timing.
   The one successful 1+7 and its failed six-minute repeat changed identity and
   waited different intervals after configuration verification.
2. Starting from observed failure, briefly take only the port-7 USB member down
   and back up; then, if still failed, rebuild the whole Linux bond with the same
   identity. Record the carrier behavior: USB admin-down does not guarantee the
   switch sees a physical link interruption.
3. If host resets do not repair it, recreate only the isolated QNAP 1+7 LAG and
   immediately start the same peer. This adds a switch-membership transition to
   the already-tested host reset, making the comparison informative.
4. Separately test a true physical-link interruption (cable or verified switch-port
   admin disable/enable). Do not label a host-only pulse a physical-link test.
5. If needed, compare fixed-identity 1+7 → known-good 1+3 → 1+7; then actor A/B/A,
   member ID/order, priorities, passive mode and directional cadence controls.

Each intervention needs a pre-action failure baseline and a post-action capture.
A transient synchronization is a useful observation but not a successful
recovery. Require at least five minutes of continuous native plus fresh reciprocal
LACP evidence, and later validate forwarding under load. A recovery seen once
must be repeated before proposing an automated production workaround.

The [peer-control analysis](peer-lacp-controls-20260906.md) compares actual frames.
No relevant malformed-frame difference was found. A real Firewalla Fast test
already failed to repair the port-4 issue; a second production bond cycle is not
an appropriate first experiment. State-aware custom cadence or an independent
OVS responder remains a later bench option, replacing the kernel sender rather
than injecting stale or contradictory PDUs alongside it.

## Isolating port 10 and management polling

Earlier tests disabled port 10, 5 and 6 separately for about 60 seconds, then
all three together for about 118 seconds, **after the fault already existed**.
They did not recover it. That weakens an ongoing downstream-traffic explanation
but cannot exclude a prior external trigger leaving persistent switch state.
See the [completed branch-cut report](port-isolation-results-20260905.md).

The stronger tests distinguish three conditions:

- **No API polling, port 10 retained:** tests whether our management requests or
  polling load contributes, without changing the external network connection.
- **No API polling, port 10 disconnected before fresh peer startup:** tests an
  isolated peer negotiation while the rest of the network cannot send traffic
  into the switch. Keep startup timing/actor equal to the preceding control.
- **Switch boot with port 10 already disconnected:** additionally removes a
  possible trigger before the peer starts. This requires deliberately persisting
  the intended bench LAG before reboot, and a native/PCAP recorder that survives
  switch unreachability. The 22:53 UTC live read found 1+7 group 4 Long after
  power-up despite no explicit Save in its preparation. Persistence semantics
  remain unverified; deliberately prepare and read back the intended state
  instead of assuming either retention or neutralization.

The existing sweep and existing-pair runner depend on switch API polling and
will abort when that path is lost. Do not unplug port 10 during that runner.
An offline-capable observe-only mode or a separately verified, direct local
management connection is required. The host's eno1 connection to the rest of
its network should remain intact; only QNAP's port-10 uplink is the experimental
cut. Ports 9/10 must remain individual, non-LAG ports.

Turning loop protection off and disconnecting the uplink in the same trial would
confound the result. Test these separately. Even a successful isolated startup
needs reconnection and repeat controls to distinguish traffic-triggered failure
from elapsed-time or configuration-history effects.

## Vendor preparation

- [QNAP report draft and verified support URL](support/qnap-lacp-report-20260906.md)
- [Firewalla engineering-review draft](support/firewalla-lacp-review-20260906.md)

Neither report has been sent. No supported peer-side fix is claimed. A useful
engineering handoff is a repeatable fail/recover/fail sequence with one changed
variable, precise versions/identities, native state and per-member captures.
