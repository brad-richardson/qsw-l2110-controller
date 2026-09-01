# qsw-l2110-controller

Experimental, declarative control of the QNAP QSW-L2110 switch family.

> **Pre-alpha:** the HTTP interface was derived from QNAP's QSS 2.2.3 firmware and
> has not yet been exercised against a physical switch. Read-only commands should
> be tested first. Do not attach an ONT or production LAN while validating writes.

The QSW-L2110 does not use the Linux-based `/api/v1` or `/api/v2` interface found
on many QSW-M switches. Its QSS 2.2.x firmware exposes a smaller, undocumented
CivetWeb interface with separate handlers for LACP, VLANs, backup, and persistence.

This project currently provides:

- model and firmware guards before planning or writing;
- read-only identity, LAG, VLAN, PVID, and live LAG-state commands;
- opaque QSS configuration backups;
- YAML-driven LAG and VLAN reconciliation;
- dry-run diffs by default;
- automatic backup, ordered LAG/VLAN writes, explicit save, and read-back verification;
- safety validation for mixed-speed groups, duplicate untagged memberships, and
  inconsistent VLAN membership among LAG members.

It deliberately does **not** delete VLANs, restore backups, update firmware, or
change the management address.

## Target status

| Target | Status |
|---|---|
| QSW-L2110-10T, QSS 2.2.3 | Firmware-derived; hardware validation pending |
| QSW-L2110-2S8T, QSS 2.2.3 | Likely same handlers; not enabled in the example guard |
| QSW-M and other `/api/v1`/`api/v2` models | Not supported |

QNAP's current firmware and release notes are available from the
[QSS 2.2 release page](https://www.qnap.com/en-us/release-notes/qss/overview/2.2).
This project is independent and is not affiliated with or supported by QNAP.

## Install for development

```console
git clone https://github.com/brad-richardson/qsw-l2110-controller.git
cd qsw-l2110-controller
uv sync --extra dev
```

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
| 5 | temporary LAN-side rescue/management port |
| 6-8 | default/local access ports |
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
  --yes-i-understand-private-api
```

Do not run that command until the read-only and canary phases in the
[hardware validation plan](docs/hardware-validation.md) pass.

## Safety model

- Only `link_aggregation.managed_ports` may have their LAG state changed.
- VLANs present in YAML are reconciled; unlisted VLANs are preserved.
- Taking an untagged port from an existing VLAN requires declaring that existing
  VLAN too, making the removal visible in the plan.
- Ports 1-8 and 9-10 cannot be mixed within one LAG.
- Every member of a LAG must have identical desired VLAN membership.
- An apply is not transactional. If connectivity is lost between LAG and VLAN
  writes, recover manually using the automatic backup and a dedicated rescue port.

See [known limitations](docs/limitations.md), [private API notes](docs/api-notes.md),
and the [Firewalla topology notes](docs/firewalla-topology.md) before deployment.

## Development

```console
uv run ruff check .
uv run pytest -q
```

Contributions with sanitized hardware observations are welcome. Never attach
passwords, MD5 login digests, cookies, public IP addresses, or configuration
backup files to an issue. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT. The license covers this project's original code and documentation, not QNAP
firmware, UI assets, trademarks, or other vendor material.
