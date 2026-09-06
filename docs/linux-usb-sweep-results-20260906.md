# Linux USB port sweep — September 6, 2026

## Checkpoint: 16/28 pairs, interrupted at 18:59 UTC

Run `usb-sweep-20260906T183251Z` used two RTL8153/r8152 USB adapters at
1000 Mb/s full duplex, QNAP group 4 Long, Linux Slow, and untagged/PVID VLAN 1
on ports 1–8. Port 10 retained management; ports 9–10 were excluded. Firewalla
configuration was not changed. The preceding 90-second 1+3 control validated
this USB peer; earlier ASIX attempts are invalid port-pair comparisons.

| Pair | Initial negotiation | Measurement (s) |
| --- | --- | ---: |
| 1+2 | Negotiated | 33.7 |
| 1+3 | Negotiated | 35.7 |
| 1+4 | Not established in window | 15.6 |
| 1+5 | Not established in window | 15.6 |
| 1+6 | Not established in window | 15.7 |
| 1+7 | Negotiated | 33.7 |
| 1+8 | Not established in window | 15.6 |
| 2+8 | Not established in window | 15.6 |
| 2+7 | Not established in window | 15.6 |
| 2+6 | Not established in window | 15.6 |
| 2+5 | Not established in window | 15.7 |
| 2+4 | Not established in window | 15.7 |
| 2+3 | Negotiated | 33.6 |
| 3+4 | Not established in window | 15.6 |
| 3+5 | Not established in window | 15.7 |
| 3+6 | Not established in window | 15.6 |

The 15-second trial setting extends native-clean pairs for fresh Slow-LACP
packet confirmation, so successful trials took approximately 34–36 seconds.
Negative quick results remain provisional. Continuous logging while the user
paused additionally showed 1+8 unsynchronized beyond 90 seconds, 2+5 beyond
100 seconds, and 3+5 beyond two minutes. These observations are outside the
trial windows. No throughput or five-minute stability test ran.

**1+7 negotiated, whereas 2+7 did not.** This disproves a blanket claim that
port 7 cannot negotiate LACP, but does not establish reliable operation on 7.
All three pairings within ports 1–3 negotiated. Pairing, configuration history,
and switch firmware/state remain candidates; this ordered sweep cannot
separate these effects. A later 1+7 → 2+7 → 1+7 repeat would be useful.

The [earlier Firewalla notes](firewalla-topology.md) did test **3+7** at
2.5 Gb/s and observed the second member defaulted with zero partner identity.
Today's Linux bench changes peer, speed, and VLAN, so it is not an exact repeat.
**Today's 3+7 has not yet been measured.**

The runner stopped during the move from 3+6 toward 3+7 on
`GET /port_setting_load.json failed`. This was a management read failure,
not a recorded LACP result for 3+7. Exit-neutral switch configuration verified;
both USB interfaces returned down with no host cleanup errors. Both tcpdump
processes exited 0; the owned link monitor was interrupted during cleanup.

Resume preserves the 16 results and starts with 3+7. Unplug both test Ethernet
cables from QNAP, retaining USB connections and management on port 10:

```bash
sudo .venv/bin/python -m tools.lacp_sweep run \
  --bench-isolated --insecure --prepare-vlan 1 \
  --interfaces enx5c857e38d8d1 enxa0cec8597422 \
  --ports 1-8 --seconds 15 --early-success \
  --resume backups/usb-sweep-20260906T183251Z
```

Wait for READY before connecting 3+7. Resume creates a new log directory;
use that newest directory for any subsequent resume.

[Sanitized trial evidence](evidence/linux-usb-sweep-20260906.json).
Raw configuration backups and packet captures remain local under `backups/`.
