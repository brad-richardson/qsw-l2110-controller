# Runbook: second LACP peer from a laptop with two USB Ethernet adapters

For the current MacBook baseline on the existing spare ports 1+2, use the
[MacBook agent handoff](macbook-lacp-agent-handoff-20260905.md). It includes
the post-recovery constraints and supersedes this runbook's test sequence.

Before running, see the later [experiment review](lacp-review-20260905.md) for
adapter checks, limits of the harness's cached sync verdict, the missing
ports-7+8 YAML, and spare-port alternatives that preserve the production LAN.

Purpose: take the Firewalla out of the picture. A laptop bond that syncs both members
on ports where the Firewalla could not points at the router; a laptop bond that hits
the same wall confirms the switch with no router involved. The same harness then maps
which switch ports can complete LACP at all.

Tool: `tools/lacp_peer_test.py`. It is a single stdlib-only file that runs on the laptop
under `sudo` with the stock `python3` (3.9 or newer) and `tcpdump`. Copy it over or clone
the repository. It never touches the switch's management interface.

Sync status is read from the LACPDUs captured on each member, so the live output is the
same on macOS and Linux. Synced means the switch's LACPDUs on that member carry in-sync,
collecting and distributing, and ours do too.

## macOS setup

1. Plug in both USB adapters and find their device names, for example `en7` and `en8`:

   ```console
   networksetup -listallhardwareports
   ```

2. Create the bond. The name is a display name; the device will be `bond0` unless
   another bond already exists, which `-listBonds` shows:

   ```console
   sudo python3 tools/lacp_peer_test.py setup --bond LagTest --member en7 --member en8
   ```

   macOS uses LACP mode by default. The new network service will try DHCP over the
   bond, which is harmless: it gets nothing in VLAN 3999 and a LAN address in VLAN 10
   once the bond forwards, which doubles as a functional check.

3. Later, remove it with `sudo python3 tools/lacp_peer_test.py teardown --bond bond0`.

Linux differs only in `setup` using `ip link`, with `--lacp-rate` and `--address`
options, and in `/proc/net/bonding` being recorded alongside the captures.

## Recording a test

Start the recorder before plugging any cable, then plug cables while it runs. It prints
one line per member each second and writes `summary.json` at the end:

```console
sudo python3 tools/lacp_peer_test.py record --bond bond0 --member en7 --member en8 \
  --duration 900 --output ~/lacp-tests/ports-3-4-run-a
```

Note cable moves from a second terminal so they land in the summary:

```console
sudo python3 tools/lacp_peer_test.py mark --output ~/lacp-tests/ports-3-4-run-a 'plugged port 4'
```

Each live line reads like `en7: SYNCED; switch port 3 key 3 state 63 [...]; ours state
61 [...]`. The switch port number in the line is the switch's own actor port from its
LACPDUs, so it tells you which switch port a given adapter landed on without guessing.

Copy the output directories into the repository's ignored `backups/` afterwards.

## Test matrix

Each run starts with both cables unplugged for at least one minute so the switch group
has no history. Wait for the first member to show SYNCED, or for three minutes, before
plugging the second. Keep the ONT off the switch throughout.

| Run | Switch ports | Switch config change | What it tests |
|---|---|---|---|
| 1 | 1+2, port 1 first | none; group 1 exists, VLAN 3999, no LAN impact | Laptop bond on the pair that worked for the WAN |
| 2 | 1+2, port 2 first | none | Order control on the good pair |
| 3 | 3+4, port 4 first | none; unplug the Firewalla from 3+4 for the run | The decisive Firewalla-versus-switch test; LAN loses its gateway during the run |
| 4 | 3+4, port 3 first | none | Order control on the failing pair |
| 5 | 7+8, port 8 first | `examples/experiments/laptop-lag-ports-7-8.yaml` | A second "bad" candidate pair with zero LAN impact |
| 6 | 7+8, port 7 first | same | Order control |

Run 5 and 6 need port 8 moved from VLAN 1 to VLAN 10 and a new LACP group on 7+8; the
YAML does that and the restore file for the LAN puts port 8 back. Port 8 is the rescue
port, so skip these two if you want to keep the VLAN 1 rescue path untouched.

After any run where a member stays defaulted, run the ingress probe from that member
with the other member as the control, then check the switch's MAC table for the test
MAC (`uv run qsw-l2110 --insecure dump-mac-table`):

```console
sudo python3 tools/lacp_peer_test.py probe --send-from en8 --member en7 --member en8 \
  --target-ip 192.168.1.72 --output ~/lacp-tests/probe-port-4
```

On Linux this uses a raw socket; on macOS it writes the frame through BPF and is marked
experimental. A reply arriving on the other member means the switch attributes the
sender's ingress to the LAG's lowest member, as seen with the Firewalla on 2026-09-05.

## Reading the results

- Laptop syncs both members on 3+4 in run 3: the Firewalla's LAN bond is implicated.
  Compare its LACPDUs with the laptop's, field by field, from the captures.
- Laptop fails on port 4 exactly like the Firewalla: switch confirmed. Runs 1, 2, 5, 6
  then map which ports work, which is the port list to give QNAP.
- Laptop syncs on the second-plugged port in some runs only: order matters after all;
  repeat before believing it.
- A member that syncs and then drops to defaulted after a few seconds, as port 4 did for
  about ten seconds after the 2026-09-05 reboot, points at a switch process that starts
  later, loop detection first among them.

## Notes

- Two 1G USB adapters negotiate 1G on the 2.5G ports; LACP does not care, but it means
  the laptop cannot reproduce any 2.5G-specific behaviour. A 2.5G USB adapter can.
- The laptop must not bridge or route between the two adapters; the bond is the only
  thing that should own them.
- Keep the two recordings per port pair, one per order, and the summaries. The
  `summary.json` files carry time-to-sync, the switch port and key seen on each member,
  and every mark, which is enough for a QNAP report without the raw captures.
