# USB 1+7 observation without switch API polling — September 6, 2026

## Result: negotiated, then lost port 7

The manual-UI observer measured 360.027 seconds on the existing 1+7 group 4
Long configuration. Both members initially synchronized, but QNAP port 7
advertised Defaulted with zero partner identity at **23:07:08.682974 UTC**.
That is **189.063 seconds after peer setup started**, or **156.596 seconds
after the first QNAP port-7 PDU advertising 61/61**. It did not recover before
the observation ended. Port 1 remained synchronized after convergence.

The longest jointly verified clean interval was **156.213 seconds**. At the
end, native state was Linux/QNAP 61/61 on port 1 and 13/69 on port 7. Both
physical links remained 1000/full with zero member link failures. The host
captured 12 outgoing Linux LACP frames on port 7 after the first defaulted
QNAP response, with the last at 23:09:45.196303 UTC. Local TX capture does not
prove delivery to the switch ingress or control plane.

The live progress update first noticed the failure around 235 seconds; packet
analysis establishes the earlier 189-second transition. This is a delayed
failure, but **not evidence of the same fixed timer** as the earlier Firewalla
mirror experiment's 219.895 seconds from first QNAP 61/61 to default.

## Controls and limits

- Same two RTL8153/r8152 USB adapters, port mapping, 1000/full speed, group 4
  Long and Linux Slow as the earlier six-minute failed repeat.
- Same Linux actor identity as that repeat: `02:3f:6c:3a:ff:a7`.
- Before starting, LAG, VLAN/PVID and port-setting signatures matched the saved
  earlier repeat configuration. Mirroring was not rechecked in this preflight.
- The switch had been power-cycled and retained 1+7. There was no factory reset
  or new QSS configuration application for this run; startup history differs.
- Preflight readback logged out successfully before peer setup. During this
  observation, neither the tool nor the agent opened a switch session, polled
  its API, changed switch settings, or ran console/debug operations. The agent
  only inspected local observation files.
- Earlier diagnostic work fetched pages and existing result values. It did not
  start TDR, eye tests, register reads/writes or a console session.
- Management uplink 10 remained connected. This is not the planned fully
  disconnected cold-start control or the factory-reset UI-only pair sweep.

Ongoing switch API polling and active console/debug tests are therefore **not
necessary triggers** for this observed failure. Earlier management activity or
network history affecting persistent state is not excluded. Power cycling did
not provide sustained recovery in this run. The same actor now produced both
an all-negative run and a transient success; actor identity alone cannot explain
that difference. No particular internal receive/forwarding defect is proven.

## Evidence and cleanup

Private run: `backups/usb-ui-sweep-20260906T230353Z/trial-001-1-7/`.
Preflight: `backups/usb-1-7-live-preflight-20260906T230319Z/`.
There were 361 native samples, maximum gap 1.010 seconds. Both PCAPs were
complete with zero kernel drops. Both tcpdump processes exited successfully;
the link monitor exited on the expected SIGINT. Host cleanup completed at
23:10:00.927782 UTC without errors and returned both USB interfaces down.
Switch configuration and Firewalla configuration were unchanged by the run.
This measures LACP negotiation, not throughput or forwarding on each member.

[Sanitized evidence](evidence/linux-usb-1-7-no-api-20260906.json).
Compare the [earlier six-minute repeat](linux-usb-1-7-repeat-20260906.md) and
[full pair sweep](linux-usb-sweep-results-20260906.md).
