# Runbook: LAN LAG on switch ports 1+2, both bring-up orders

Purpose: decide whether the one-member LAN LAG failure follows the switch ports or the
bring-up order. Ports 1+2 carried a working two-member WAN LAG on 2026-09-05, but both
times the higher port came up first. Ports 3+4 have only ever come up lowest-first.
This runbook moves the LAN LAG to ports 1+2 and brings the pair up in both orders.
Later on 2026-09-05, port 4 also failed to sync while it was the only connected member
after a full teardown, and a dummy LACP group on the neighbouring port changed nothing,
so the expected answer is now port-specific; the two orders remain as a control.

Configuration files:

- Target: `examples/experiments/lan-lag-ports-1-2.yaml`
- Restore: `examples/experiments/lan-lag-ports-3-4-short.yaml` (LAN group 4 on ports 3+4,
  Short timeout, the state saved on the evening of 2026-09-05). The `-restore.yaml`
  variant is the earlier Long-timeout state and is kept for reference only.

Expected plan for the target, verified on 2026-09-05 16:40 UTC: ports 3 and 4 leave
their LAG, ports 1 and 2 move from untagged VLAN 3999 to untagged VLAN 10, and their
PVIDs follow. Six changes, nothing else. The restore plan is empty until the target is
applied.

## Preconditions

- The WAN is a direct ONT-to-`eth0` link. Switch ports 1, 2, and 9 have no cable.
  Do not run this while anything WAN-side is on the switch.
- Firewalla LAN is `bond0` over `eth2`+`eth3`, cabled to switch ports 3 and 4.
- The observer reaches the switch directly on the LAN, not through the Firewalla.
- Expect the LAN to lose its gateway for a few minutes at each cable move. Nothing on
  the switch side needs the Firewalla to be reachable.

Shell setup, values omitted here on purpose:

```console
export QSW_HOST=https://switch.example QSW_USER=admin QSW_PASSWORD='...'
export FW_SSH=pi@router.example FW_KEY=~/.ssh/router_capture
alias fwstate='ssh -i "$FW_KEY" -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes "$FW_SSH" \
  "sudo -n grep -E \"^Slave Interface|Aggregator ID|Partner Key|port number|port state\" /proc/net/bonding/bond0"'
```

`fwstate` prints each slave's aggregator, the switch port it identifies, and the
actor/partner state bytes. Synced and forwarding is 61 on the actor side and 61 or 63
on the partner side. A defaulted partner shows 69 or 71. Judge results from this, not
from the switch's LAG page.

## Step 0: record and back up

```console
uv run python -m tools.firewalla_recorder start --ssh-target "$FW_SSH" \
  --identity-file "$FW_KEY" --interface eth2 --interface eth3 --bond bond0 \
  --duration 2400 --label lan-ports-1-2 --output backups/lan-ports-1-2/recorder
uv run qsw-l2110 --insecure backup backups/lan-ports-1-2/before.cfg
uv run qsw-l2110 --insecure plan -f examples/experiments/lan-lag-ports-1-2.yaml
```

Stop if the plan shows anything beyond the six expected changes.

## Step 1: apply, then move both cables

```console
uv run qsw-l2110 --insecure apply -f examples/experiments/lan-lag-ports-1-2.yaml \
  --backup-dir backups/lan-ports-1-2 --yes-i-understand-private-api \
  --yes-i-validated-vlan-transitions
```

The LAN keeps working through port 3 as a plain access port for about 90 seconds after
the apply, because the Firewalla holds its last partner record that long. Unplug both
LAN cables from ports 3 and 4 right after the apply completes. Leave both out for at
least two minutes so both sides fully time out and the switch group has no history.

## Run A: lowest port first, the order that fails on 3+4

1. Plug one LAN cable into port 1. Wait up to three minutes. `fwstate` should show one
   slave at 61/63 identifying partner port 1, and the LAN gateway should answer.
   A lowest member alone has synced every time so far; if it does not, stop and
   restore, that is a new result.
2. Plug the second cable into port 2. Wait three minutes. Record `fwstate`.
3. If the port-2 slave stays defaulted, run the ARP probe from it, replacing `ethX`
   with that slave and `ethY` with the synced one:

   ```console
   uv run python -m tools.arp_ingress_probe --ssh-target "$FW_SSH" --identity-file "$FW_KEY" \
     --interface ethX --control-interface ethY --target-ip <switch IP> \
     --output backups/lan-ports-1-2/arp-run-a
   uv run qsw-l2110 --insecure dump-mac-table | grep -A3 -i '02:00:00:00:00:4'
   ```

   A reply on `ethY` with the test MAC learned on port 1 means ports 1+2 show the same
   ingress attribution as ports 3+4.

## Teardown between runs

Unplug both cables. Wait at least two minutes. Confirm with `fwstate` that both slaves
show a defaulted partner before continuing.

## Run B: higher port first, the order that has always worked

1. Plug one cable into port 2. Wait for 61/63 identifying partner port 2 and a
   working gateway.
2. Plug the second cable into port 1. Wait three minutes. Record `fwstate`.

## Reading the result

| Run A (port 1 first) | Run B (port 2 first) | Meaning |
|---|---|---|
| both sync | both sync | The defect follows ports 3, 4, and 7. Leave the LAN LAG on 1+2 as the workaround and report the port set to QNAP. |
| second member defaulted | both sync | Bring-up order is the defect, independent of ports. Every switch reboot will need a manual bring-up order. |
| second member defaulted | second member defaulted | LAN-specific: VLAN 10, the traffic in it, or the Firewalla LAN bond. Next split: unplug the office trunk on port 10 and repeat Run B with a quiet VLAN. |
| both sync | second member defaulted | Order-dependent in the opposite direction; repeat both runs before believing it. |

## Restore

Only if the LAN is going back to ports 3+4. If Run A and Run B both passed, keeping the
LAN on ports 1+2 is a valid end state; then skip the restore and update the examples.

1. Unplug both cables from ports 1 and 2. Plug them into ports 3 and 4.
2. Apply the restore file and confirm the plan is empty afterwards:

   ```console
   uv run qsw-l2110 --insecure apply -f examples/experiments/lan-lag-ports-3-4-short.yaml \
     --backup-dir backups/lan-ports-1-2 --yes-i-understand-private-api \
     --yes-i-validated-vlan-transitions
   uv run qsw-l2110 --insecure plan -f examples/experiments/lan-lag-ports-3-4-short.yaml
   ```

3. Fetch the recording:

   ```console
   uv run python -m tools.firewalla_recorder fetch --output backups/lan-ports-1-2/recorder
   ```

## Notes

- No automatic rollback timer is used here. A switch-side rollback while the cables
  are in ports 1+2 would isolate the LAN rather than restore it. If a deadline timer
  is wanted anyway, run it from `systemd-run --user` with the absolute path to `uv`;
  the user service PATH does not include `~/.local/bin`, which is what defeated the
  2026-09-05 watchdog.
- The switch forwards on a defaulted lowest member. Never bridge two switch ports on
  the Firewalla side while one of them is a LAG member; that made an L2 loop on
  2026-09-05.
- The Firewalla side needs no change. Do not edit Firewalla-managed files over SSH.
