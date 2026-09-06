# IoT Wi-Fi isolation — 2026-09-05

**Turning off both IoT Wi-Fi networks did not clear the existing LAN LACP failure during the following five minutes.** The user performed the Wi-Fi change; the agent made no device-block, AP, switch, or Firewalla configuration changes during this observation.

This followed the [single-LAG test](lacp-single-group-and-events-20260905.md). The switch remained on QSS 2.2.3.20260713 with only production LACP group 4 enabled on ports 3+4, Long. Port 4 was already defaulted before the Wi-Fi change.

The continuous [five-second watcher](../tools/lan_lacp_watch.py) started before the user confirmed the change. A separate passive recorder collected conntrack events, neighbor/link notifications, ARP/DHCP packets, flow logs, and before/after state. The user's shutdown report was marked at **2026-09-06 01:02:04.974038 UTC** (September 5, 21:02:04 EDT). The user immediately clarified that “ios” meant **IoT** Wi-Fi. This timestamp records receipt of the report, not an independently measured AP apply time.

The selected observation is the 300 seconds following that marker, ending **01:07:04.974038 UTC**. Fresh member packets and privileged native state continued to agree: `eth2` / switch port 3 remained 61/61, while `eth3` / port 4 remained 13/69. There was no jointly clean interval or observed recovery. The machine-readable [evidence summary](evidence/lacp-iot-wifi-isolation-20260905-summary.json) records exact sample/packet counts and final capture integrity.

This weakens the hypothesis that ongoing traffic exclusively from those Wi-Fi networks is necessary to **maintain** the failure. It does not exclude that traffic as an earlier **trigger**: the switch or peer might remain in a failed runtime state after the triggering condition disappears. It also does not isolate wired IoT devices, clients on other SSIDs, or traffic from protected devices. No endpoint was identified as defective.

The earlier three firmware boots all defaulted port 4 approximately 237 seconds after its first fully clean QNAP PDU. A useful later control would start with IoT Wi-Fi already disabled, reset switch runtime state in a planned interruption, and observe beyond that transition time before restoring the SSIDs. That control was **not performed** in this observation.

The user was told the five-minute observation was complete and that IoT Wi-Fi could be re-enabled. Protected endpoints remain the user's Mac, all Apple devices, and bradflix, plus Firewalla/AP/switch infrastructure and uplinks. The agent did not disconnect or block any of them.

Raw captures, flow/device records, and user event markers remain ignored under `backups/iot-wifi-isolation-20260905/`. The watcher is available for further manual experiments from the repository:

```console
.venv/bin/python -m tools.lan_lacp_watch
```

The watcher observes only, timestamps typed notes, and checks the current uninterrupted clean interval rather than reusing a past successful status. Ctrl-C stops its owned recorder and finalizes capture counters.
