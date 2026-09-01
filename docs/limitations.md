# Known limitations

## Project maturity

- No command has yet been run against physical QSW-L2110 hardware.
- Endpoint and payload knowledge comes from static inspection of the official
  QSS 2.2.3 build 20260713 firmware image.
- QNAP does not publish this as a supported API. Any firmware update may change it.
- Only QSW-L2110-10T is enabled in the example model guard.
- The guard requires the exact `2.2.3.20260713` firmware string inferred from
  the image. A different live string requires renewed inspection, not a prefix match.

## Configuration behavior

- Applies are ordered but not transactional: LAG changes occur before VLAN changes.
- There is no distributed lock or compare-and-swap API. Treat the switch as
  single-writer and close other QSS/controller sessions during plan/apply.
- The switch may apply running configuration before the explicit global save.
- Automatic rollback is not implemented. Each apply downloads an opaque backup first.
- Backups are created exclusively with mode `0600`, are never silently overwritten,
  and are reported with a SHA-256 checksum.
- Backup restore is intentionally absent because it reboots and replaces all configuration.
- VLAN deletion is intentionally absent. Unlisted VLANs are preserved.
- PVID writes are intentionally absent until `/port_vlan.json` transitions are
  captured on hardware. Drift is shown in plans and must converge before save.
- Multi-destination VLAN writes and destination-before-source ordering are still
  unverified. Untagged moves require a separate post-canary acknowledgement.
- Management IP, HTTPS, certificates, users, firmware, port speed, QoS, and loop
  protection are outside the initial declarative scope.
- The firmware UI renders VLAN membership for ten physical ports, not logical LAG
  objects. The validator therefore requires identical VLAN membership on every
  member, but actual ASIC behavior still needs validation.
- The firmware UI enforces at most 64 VLAN entries, IDs 1-4094, names up to 16
  characters, and one untagged VLAN per port.
- The optional Firewalla policy protects the shipped topology's WAN, LAN, office,
  ONT, and rescue roles; generic configurations without that policy do not gain
  topology-specific isolation guarantees.

## Authentication and transport

- Login places MD5 digests of both username and password in URL query parameters.
  The digests are replayable credentials, not safe password protection.
- URLs may be retained by proxies, browser history, diagnostics, or access logs.
- Firmware inspection found session cookies without a `Secure` attribute. Live
  behavior must be confirmed.
- HTTPS is required by default. Initial self-signed certificates need explicit
  trust through `--ca-bundle`, or temporary lab-only `--insecure` operation.
- Clear-text HTTP requires the explicit `--allow-http` override.

## Observability and standards

- This Lite Managed switch does not provide supported end-user SSH, SNMP, LLDP,
  RSTP, or ACL management.
- No selectable LACP transmit-hash algorithm was found in the QSS 2.2.3 UI.
- LACP aggregates flows; one flow remains limited to one member.
- The VLAN listing uses Server-Sent Events and appears to signal completion by
  closing the stream rather than sending an explicit final object.
- The client rejects SSE timeouts and cross-checks clean reads against the
  separate VLAN-ID list and stable PVID snapshots.
- `/port_trunk_refresh.json` appears to expose link up/down, not full LACP
  collecting/distributing state.

## Contract emulator

- The loopback emulator independently implements the inferred request/response
  shapes and catches controller sequencing, validation, and verification bugs.
- It has no switch ASIC, LACP peer, forwarding plane, vendor TLS stack, reboot,
  flash persistence, or firmware parser. Passing it cannot validate those behaviors.
- Its successful path models the possibility that a VLAN write updates PVIDs;
  a fault test also holds PVIDs stale and proves the controller refuses to save.

## Shared-switch firewall topology

- WAN and LAN share a chassis, power supply, firmware, configuration database,
  switch fabric, and management plane.
- A default configuration, restore error, or VLAN mistake can bridge the ONT to
  the LAN and bypass the firewall. This is a fail-open design.
- LACP protects a cable/member port; it does not protect against switch failure.
- The QNAP-side hash may pin ONT-to-Firewalla traffic to one 2.5G member.
- Firewalla WAN LAG does not support a VLAN interface. PPPoE and switch-side ISP
  tag removal require separate validation.
- A single 10G WAN and LAN connection is preferable on Firewalla Gold Pro-class
  hardware.

Do not treat this project as a substitute for an enterprise switch with a public
API, configuration rollback, management-plane ACLs, and independently tested
failure behavior.
