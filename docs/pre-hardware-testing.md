# Pre-hardware testing

The repository can test controller logic and the inferred HTTP contract before a
switch is available. It cannot test the physical forwarding plane or prove that
the firmware behaves like the static image suggests.

## Automated checks

Create the project-local Python 3.14 environment and run the same checks as CI:

```console
uv venv --python 3.14
uv sync --locked --extra dev
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
```

The tests cover strict YAML parsing, topology policy, LAG/VLAN reconciliation,
unmanaged-state guards, payload ordering, exact identity checks, authentication,
SSE truncation, inventory consistency, backup behavior, and failed read-back.

## Stateful QSS contract emulator

`tools/qss_emulator.py` is an independent standard-library implementation of the
firmware-derived routes. It binds only to `127.0.0.1` and deliberately suppresses
HTTP request logging so MD5 credential digests are not printed.

Start it in one terminal:

```console
uv run python tools/qss_emulator.py
```

Use the real installed CLI from another terminal:

```console
export QSW_PASSWORD=emulator-only
uv run qsw-l2110 --host http://127.0.0.1:8765 --allow-http about
uv run qsw-l2110 --host http://127.0.0.1:8765 --allow-http dump-vlans
uv run qsw-l2110 --host http://127.0.0.1:8765 --allow-http plan \
  --config examples/firewalla-gold-plus.yaml
```

An emulator-only apply exercises backup, full LAG POST, fresh VLAN inventory,
destination-before-source VLAN POST, PVID verification, save, and final read-back:

```console
uv run qsw-l2110 --host http://127.0.0.1:8765 --allow-http apply \
  --config examples/firewalla-gold-plus.yaml \
  --backup-dir /tmp/qsw-emulator-backups \
  --yes-i-understand-private-api \
  --yes-i-validated-vlan-transitions
```

The automated emulator suite also injects ignored LAG writes, ignored VLAN
writes, VLAN writes that leave ingress PVIDs stale, and a rejected save request.
Failed convergence must stop before save; a rejected save must surface an error
without claiming persistence.

## What this proves

- The CLI, parser, planner, client, and verification stages compose correctly.
- Credential digests, cookies, JSON, SSE, and opaque backups cross an actual HTTP socket.
- Reads remain read-only, backups precede writes, stale state aborts, and failed
  convergence does not reach the controller's save request.
- The shipped Firewalla profile rejects configurations that violate the declared
  WAN-transit, LAN-trunk, and rescue-port isolation policy.

## What remains hardware-only

- Exact live response fields, cookie behavior, TLS, content types, and error objects.
- Whether a tag-VLAN POST updates ingress PVID, and the required `/port_vlan.json` sequence.
- Whether multi-entry VLAN writes are atomic and honor destination-before-source ordering.
- Running-versus-saved configuration, reboot persistence, and backup restoration.
- Management-plane reachability and VLAN isolation.
- LACP negotiation, collecting/distributing state, hash behavior, member failover,
  throughput, and all ASIC forwarding behavior.

The emulator encodes the same inferred contract as the client. Agreement between
them is not independent evidence about QNAP hardware. Continue with the
[hardware validation plan](hardware-validation.md), in order, with the ONT and
office trunk disconnected.
