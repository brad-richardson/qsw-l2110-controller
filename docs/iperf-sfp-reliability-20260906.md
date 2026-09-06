# Throughput baseline and reported SFP-swap outage — September 6, 2026

The pre-swap baseline measured **2.35 Gb/s in both directions**, with four TCP
streams for 60 measured seconds per direction after three seconds of warm-up.
Both LAN LAG members remained healthy during load. The temporary iperf server
and passive recorder were stopped and audited at **14:35:01 UTC**. The user was
told the baseline was complete and the cable swap could proceed.

**The user reported that attempting the SFP swap took down the network. No
post-swap iperf or longer reliability test was run.** The specific cable endpoints,
SFP+ module/adapter, and physical reversion to the original path are awaiting
clarification. Reachability had recovered by the first post-report check.
This report's benchmark topology describes the baseline only. No router or
switch network configuration was changed by the agent for either the benchmark
or the post-outage checks.

## Baseline path and method

- Linux observer `eno1`, **2.5 Gb/s**, MTU 1500. The switch learned its MAC on
  **QNAP port 10, VLAN 10**; the route to Firewalla was directly connected.
- QSW-L2110-10T, QSS **2.2.3.20260713**, group **4 on ports 1+2**, Long.
- Existing Firewalla `bond0`, eth2 → QNAP 1 and eth3 → QNAP 2, both 2.5 Gb/s.
  Slow LACP and `layer2+3` transmit hashing were retained.
- Client iperf3 **3.16**, extracted from Ubuntu packages into the ignored run
  directory; no system package or service installation. Firewalla used its
  existing iperf3 **3.9**. A temporary server listened only on the LAN address,
  TCP port 5209, with a five-minute systemd runtime limit.
- Each direction used `-P 4 -t 60 -O 3 -J --get-server-output`; the second
  used `-R`. These select parallel streams, a warm-up omission, JSON results,
  and reverse sending. See the [iperf documentation](https://software.es.net/iperf/invoking.html).

The recorded window was **14:31:12–14:33:29 UTC**. Router-native bond and link
counters were sampled about once per second, with non-promiscuous LACP captures
on both members. Host counters and QNAP port statistics were sampled about every
six seconds while the benchmark ran. Counters were read without clearing them.

## Results

| Direction | Receiver throughput | Member carrying bulk data | TCP retransmissions |
|---|---:|---|---:|
| Observer → Firewalla | **2.353 Gb/s** | eth2 and eth3, approximately 50/50; about 9.69 GB received on each over the phase including warm-up | 172 |
| Firewalla → observer | **2.353 Gb/s** | eth3 / QNAP port 2; about 19.38 GB transmitted over the phase including warm-up | 1 |

Neither direction had a zero-byte reporting interval. Measured per-second rates
were 2.334–2.387 Gb/s uploading and 2.338–2.355 Gb/s downloading. Interface byte
rates include protocol overhead, warm-up, and background traffic; the throughput
figures above are iperf's receiver measurements.

Firewalla's `layer2+3` policy keeps traffic for a given peer on one transmitting
member. The reverse result is consistent with that policy even with four TCP
streams; it does not indicate a failed second member. The switch's traffic toward
Firewalla demonstrably used both members. See the
[Linux bonding documentation](https://docs.kernel.org/networking/bonding.html#xmit-hash-policy).

- All **133 native samples** were clean at 61/61 on both members, in one
  two-member aggregator, over **135.594 seconds**. Maximum sample gap: 2.033 s.
- All captured LACPDUs had clean actor and partner states. Fresh reciprocal
  packets and native state were jointly valid for the final **108.401 seconds**;
  the initial capture window first had to see each peer's periodic PDU.
- The evaluator's `pass` field is false because it requires **five minutes**;
  this intentionally shorter benchmark does not meet that duration. It recorded
  no LACP regression. The earlier [longer negotiation test](lan-ports-1-2-results-20260906.md)
  separately passed that criterion.
- **Zero increases** in host and Firewalla NIC error/drop/CRC/missed-packet
  counters, host carrier-change count, and either Firewalla link-failure count.
  Both Firewalla link-failure counts stayed at 9.
- **Zero increases** in QNAP RxBadPkt or TxBadPkt on every port. TCP
  retransmissions were nonzero as listed above; preserve these as the comparison
  baseline rather than describing the transfer as lossless.
- Both tcpdump logs reported **zero kernel drops**. Each file contained all
  279 frames reported captured, including nine decoded LACPDUs.
- Bond identity, members, rate, mode, and hash policy were unchanged. Both
  owned units were inactive/collected, the test listener was closed, and no
  experiment-owned process remained after cleanup.

See [sanitized evidence](evidence/iperf-baseline-20260906.json). Raw iperf JSON,
snapshots, captures, temporary client packages, and the private launcher remain
under `backups/iperf-baseline-20260906T142620Z/` on the Linux observer.

## Reported outage and subsequent checks

The user reported the outage after attempting the SFP cable swap and suspected
the module was dead. The exact time, duration, and failed segment were not
established. The baseline's test traffic and recorders had already stopped.

- At **14:44:50 UTC**, gateway, QNAP, and internet probes each received 2/2
  replies. The observer's link was up at 2.5 Gb/s full duplex, with carrier-change
  count **10**, unchanged from the baseline.
- At **14:46:16 UTC**, both Firewalla members were clean at **61/61**, 2.5 Gb/s,
  in the same two-member aggregator. Both link-failure counts remained **9**.
  Bond identity/settings and NIC error/drop/CRC/missed-packet counters were
  unchanged from the end of the baseline.
- Available kernel logs from 14:33:30 through that read contained no relevant
  bond/NIC link events after excluding ordinary firewall traffic logs.
- QNAP still reported 2.5 Gb/s links on **1, 2, 5, 6, and 10**, the observer
  learned on port 10/VLAN 10, and no increase in any port's RxBadPkt or TxBadPkt.

These checks show a healthy LAN bond and reachable network afterwards. They do
not prove continuous LACP health during the unrecorded swap, establish that the
old physical path was restored, or diagnose a dead transceiver. Investigate the
SFP path independently before attempting another loaded run. Module/port
compatibility and matching supported link speeds are standard checks; see
[Cisco's interface troubleshooting guidance](https://www.cisco.com/c/en/us/support/docs/routers/asr-1000-series-aggregation-services-routers/200633-Troubleshooting-interface-down-issues.html).
That general guidance does not establish compatibility for the unidentified hardware.

The [sanitized post-outage evidence](evidence/sfp-swap-outage-20260906.json)
separates the user's report from the later observations. Private readbacks are
under `backups/sfp-outage-check-20260906T144609Z/`. No new test server or recorder
was started, and the agent made no network configuration changes.

## Resume the SFP investigation

1. Establish which physical path is currently connected, then identify both
   endpoints, their ports, and the SFP+ module/adapter involved in the outage.
   Check that combination's supported speeds and port configuration before
   proposing another swap. The QSW-L2110-10T itself has copper ports; do not
   infer which external SFP segment is involved.
2. Refresh the host route, switch MAC table, relevant negotiated link speeds,
   bond membership/state, and starting error counters. Verify traffic actually
   traverses the intended new segment before interpreting the result.
3. Repeat the same two-direction benchmark, then run a longer bounded load test
   while recording per-member traffic, native LACP, fresh PDUs, error counters,
   and link changes. Stop and inspect a regression instead of changing Firewalla
   networking. The user's prohibition on modifying the Firewalla LAG still applies.
4. Compare throughput, interval stalls, retransmissions, hardware errors, and
   link events with this baseline. Audit and stop the owned server/recorders,
   then update this report with the actual new path and measured duration.

The current observer NIC is limited to **2.5 Gb/s**. This setup can exercise a
10 Gb/s intermediate segment at that offered load; it cannot establish full
10 Gb/s throughput or long-term reliability from this short baseline. Reverse
bulk forwarding on eth2 also remains untested with this single endpoint pair.
