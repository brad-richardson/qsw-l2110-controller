# qsw-l2110-controller

Experimental, declarative control of the QNAP QSW-L2110 switch family.

> **Pre-alpha:** the HTTP interface was derived from QNAP's QSS 2.2.3 firmware and
> has been exercised on a QSW-L2110-10T, including VLAN transitions and saved
> configuration. Two-member LACP forwarding with Firewalla remains unresolved;
> see the [diagnostic report](docs/lacp-diagnostics-20260905.md).

The QSW-L2110 does not use the Linux-based `/api/v1` or `/api/v2` interface found
on many QSW-M switches. Its QSS 2.2.x firmware exposes a smaller, undocumented
CivetWeb interface with separate handlers for LACP, VLANs, backup, and persistence.

This project currently provides:

- model and firmware guards before planning or writing;
- read-only identity, LAG, VLAN, PVID, port setting, link, packet counter,
  MAC table, and uptime commands;
- gated deletion of a single VLAN that owns no untagged port or PVID;
- opaque QSS configuration backups;
- YAML-driven LAG and VLAN reconciliation;
- dry-run diffs by default;
- automatic backup, ordered LAG/VLAN writes, explicit save, and read-back verification;
- safety validation for mixed-speed groups, duplicate untagged memberships, and
  inconsistent VLAN membership among LAG members;
- an optional Firewalla double-LACP policy that enforces exact WAN-transit
  membership, LAN/office trunk parity, and a dedicated rescue port.

It deliberately does **not** restore backups, update firmware, or
change the management address.

## Target status

| Target | Status |
|---|---|
| QSW-L2110-10T, QSS 2.2.3 build 20260713 | Reads, configuration writes, and persistence exercised; LACP interoperability unresolved |
| QSW-L2110-2S8T, QSS 2.2.3 | Likely same handlers; not enabled in the example guard |
| QSW-M and other `/api/v1` or `/api/v2` models | Not supported |

QNAP's current firmware and release notes are available from the
[QSS 2.2 release page](https://www.qnap.com/en-us/release-notes/qss/overview/2.2).
This project is independent and is not affiliated with or supported by QNAP.

## Install for development

```console
git clone https://github.com/brad-richardson/qsw-l2110-controller.git
cd qsw-l2110-controller
uv venv --python 3.14
uv sync --locked --extra dev
```

The project targets Python 3.14, the latest stable feature line at project
inception, with upstream support through October 2030. The unqualified `3.14`
selector lets `uv` choose an up-to-date 3.14.x patch release.

Credentials are read from the environment or an interactive prompt. A password
command-line option is intentionally not provided.

```console
export QSW_HOST=https://169.254.100.101
export QSW_USER=admin
export QSW_PASSWORD='replace-me'
```

For the switch's initial self-signed certificate, use `--insecure` only in an
isolated lab. The intended steady state is `--ca-bundle switch-certificate.pem`.

## Read-only first use

```console
uv run qsw-l2110 --insecure about
uv run qsw-l2110 --insecure dump-lags
uv run qsw-l2110 --insecure dump-vlans
uv run qsw-l2110 --insecure backup backups/before.cfg
uv run qsw-l2110 --insecure dump-ports
uv run qsw-l2110 --insecure dump-stats
uv run qsw-l2110 --insecure dump-mac-table
uv run qsw-l2110 --insecure system-status
```

Clear-text HTTP is refused by default because authentication puts replayable MD5
credential digests in the URL. `--allow-http` exists only for isolated recovery
or protocol investigation.

## Plan and apply

Start from [`examples/firewalla-gold-plus.yaml`](examples/firewalla-gold-plus.yaml).
The example uses this layout:

| Ports | Purpose |
|---|---|
| 1+2 | LACP group 1, Firewalla WAN, untagged WAN-transit VLAN 3999 |
| 3+4 | LACP group 2, Firewalla LAN trunk |
| 5 | LAN-native access/test port |
| 6-7 | LAN access ports (flat-LAN example) or default/local ports |
| 8 | dedicated VLAN 1 rescue port until management behavior is proven |
| 9 | 10G ONT port, untagged WAN-transit VLAN 3999 |
| 10 | 10G office-switch LAN trunk |

Planning is read-only:

```console
uv run qsw-l2110 --insecure plan --config examples/firewalla-gold-plus.yaml
```

Writes require a separate subcommand and an explicit acknowledgement. `apply`
always downloads a backup before its first write.

```console
uv run qsw-l2110 --insecure apply \
  --config examples/firewalla-gold-plus.yaml \
  --yes-i-understand-private-api \
  --yes-i-validated-vlan-transitions
```

Declarative YAML never deletes VLANs. Remove an unwanted VLAN explicitly once
no port is untagged in it and no port uses it as PVID; VLAN 1 is always refused:

```console
uv run qsw-l2110 --insecure delete-vlan 4093 --yes-i-understand-private-api
```

The second acknowledgement is required when ingress VLAN ownership moves. The
disconnected VLAN/PVID transition canary in the
[hardware validation plan](docs/hardware-validation.md) passed on QSS
2.2.3.20260713 on 2026-09-04; the switch updates PVIDs itself. Re-run the canary
before trusting the flag on any other firmware build.

## Safety model

- Only `link_aggregation.managed_ports` may have their LAG state changed.
- VLANs present in YAML are reconciled; unlisted VLANs are preserved.
- Taking an untagged port from an existing VLAN requires declaring that existing
  VLAN too, making the removal visible in the plan.
- Ports 1-8 and 9-10 cannot be mixed within one LAG.
- Every member of a LAG must have identical desired VLAN membership.
- The example's `safety.firewalla_double_lacp` policy is checked against both
  desired VLANs and preserved VLANs discovered on the switch.
- VLAN SSE data is cross-checked against a separate VLAN-ID list and stable PVID
  snapshots; timeouts and partial inventories fail closed.
- PVID drift is planned and verified before save. PVID writes are not implemented.
- An apply is not transactional. If connectivity is lost between LAG and VLAN
  writes, recover manually using the automatic backup and a dedicated rescue port.

See [known limitations](docs/limitations.md), [private API notes](docs/api-notes.md),
and the [Firewalla topology notes](docs/firewalla-topology.md) before deployment.

## Development

Reusable [diagnostic tools](docs/diagnostic-tools.md) capture QSS response bodies,
rendered forms and screenshots, collect LACP over SSH, and temporarily mirror
ingress to a dedicated receiver. Raw artifacts stay in ignored private directories.

The [firmware comparison preparation](docs/firmware-comparison-preparation-20260905.md)
verifies downloaded images, saves read-only snapshots, and rehearses the version
sequence in memory. It exposes no firmware execution command.
The [current Long baseline](docs/lacp-long-baseline-20260905.md) records the applied
timeout setting on active pairs 1+2 and 3+4, connectivity checks, and refreshed plan.
The separately armed [unattended firmware runner](docs/firmware-unattended-runner-20260905.md)
adds raw upload, bounded configuration repair/recovery, and native/packet LAN LACP checks.

```console
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
```

The suite includes a stateful loopback QSS contract emulator and fault tests. It
can validate the complete HTTP workflow before the switch arrives, but it cannot
validate ASIC behavior or prove that physical firmware matches the inferred
contract. See [pre-hardware testing](docs/pre-hardware-testing.md).

`qsw_l2110.client.QswL2110Client` is intentionally a low-level protocol client.
Its mutating methods bypass the CLI's model, backup, ordering, and verification
gates; supported safety orchestration is currently CLI-only.

Contributions with sanitized hardware observations are welcome. Never attach
passwords, MD5 login digests, cookies, public IP addresses, or configuration
backup files to an issue. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT. The license covers this project's original code and documentation, not QNAP
firmware, UI assets, trademarks, or other vendor material.
