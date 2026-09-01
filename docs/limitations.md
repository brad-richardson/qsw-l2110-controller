# Known limitations

## Project maturity

- No command has yet been run against physical QSW-L2110 hardware.
- Endpoint and payload knowledge comes from static inspection of the official
  QSS 2.2.3 build 20260713 firmware image.
- QNAP does not publish this as a supported API. Any firmware update may change it.
- Only QSW-L2110-10T is enabled in the example model guard.

## Configuration behavior

- Applies are ordered but not transactional: LAG changes occur before VLAN changes.
- The switch may apply running configuration before the explicit global save.
- Automatic rollback is not implemented. Each apply downloads an opaque backup first.
- Backup restore is intentionally absent because it reboots and replaces all configuration.
- VLAN deletion is intentionally absent. Unlisted VLANs are preserved.
- Management IP, HTTPS, certificates, users, firmware, port speed, QoS, and loop
  protection are outside the initial declarative scope.
- The firmware UI renders VLAN membership for ten physical ports, not logical LAG
  objects. The validator therefore requires identical VLAN membership on every
  member, but actual ASIC behavior still needs validation.
- The firmware UI enforces at most 64 VLAN entries, IDs 1-4094, names up to 16
  characters, and one untagged VLAN per port.

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
