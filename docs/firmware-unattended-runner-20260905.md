# Unattended firmware comparison runner — 2026-09-05

`tools/firmware_runner.py` implements the live comparison requested after the hardware-free preparation. It records a 450-second baseline on 2.2.3.20260713, then uploads 2.2.2.20260520, 2.2.1.20260417, and finally 2.2.3.20260713. Each returned build receives its own 450-second LAN LACP observation. The user authorized live execution after preparation and an independent subagent review. Results of a live run belong in a separate report; this document describes the runner and its validation.

Both active LACP pairs remain Long, with production on ports 3+4. The runner checks production Firewalla `bond0`; it does not create a Mac bond or move cables. A fresh privileged read mapped Firewalla `eth2` to QNAP port 3 and `eth3` to port 4. Before flashing, port 3 had native actor/partner states 61/61, while port 4 had 13/69. Both physical links were 2.5 Gb/s full duplex.

## Management and recovery path

The Mac reaches QNAP 192.168.1.72 directly on its Wi-Fi subnet, without routing through Firewalla. QNAP learned the Mac's Wi-Fi MAC on ordinary LAN **port 6**. The runner rechecks direct `en0` routing and rejects ingress through an active LAG before a normal run. Therefore a failure of the Firewalla LAG can remove internet/inference access while leaving local QNAP management available. This is a topology inference supported by current route/MAC evidence, not a destructive outage test. Switch reboot, a changed management address, a VLAN reset, or failure of the AP path can still remove management access.

The runner starts in a detached local process with a private log, fsynced journal/status, and `caffeinate`. It needs no inference, download, password prompt, SSH agent, or cloud operation after launch. Keep the Mac powered and awake; this is not designed to survive a Mac reboot. All three verified images are loaded locally before writes. A transient Firewalla systemd recorder independently captures native bond state every second and both members' LACPDUs, including during LAN/SSH outages. It uses non-promiscuous capture and makes no Firewalla network configuration changes. It stops on cleanup or expires after two hours if unreachable.

The user-authorized Firewalla key was copied from bradflix's `network-observatory` project into an ignored local directory, together with that host's existing trusted Firewalla host entries. Files have mode 0600. SSH uses strict host checking, batch mode, and `IdentityAgent=none`.

Firewalla documents Bluetooth network configuration when local access fails: keep the phone on cellular data, enable Bluetooth, stay near the Gold, and retain its security dongle. [Official recovery instructions](https://help.firewalla.com/hc/en-us/articles/360053012534-How-to-access-the-box-when-the-internet-connectivity-is-lost). Bluetooth repairs the Firewalla side; QNAP firmware/configuration recovery still requires QNAP management access. Removing a Firewalla LAG may also require matching switch-side changes, so it is not a guaranteed complete recovery by itself.

## Upload, restore, and stop behavior

The plan pins the switch model/MAC, HTTPS host, complete supported configuration hash, exact image catalog, version sequence, and router-member mapping. `start`, `run`, and `recover` require both its exact SHA-256 and `--yes-flash-shared-switch`. `prepare` and `preflight` do not POST to QSS. One local lock serializes the entire live run; independently opened QSS browser tabs or other controllers must also remain idle.

The upload follows the inspected QSS UI: one firmware precheck POST, then sequential raw 12,000-byte HTTPS chunks with the authenticated session cookie. Every chunk intent and acknowledgement is journaled. The final response must report verification success, and the runner must subsequently read the exact target build on the same switch. Firmware requests are never retried after uncertainty. A lost response, redirect, missing final success, or boot deadline stops further firmware writes. The UI's own retry behavior is deliberately not reproduced. Hardware downgrade acceptance and private-handler behavior remain empirical questions for the live run.

After boot, the runner compares the complete supported configuration. If only LAG/VLAN settings drift, it backs up again, checks for concurrent drift, reapplies the full LAG table if needed, rebuilds ordered VLAN repairs from fresh state, and requests save only after exact full readback. It checks again after save. This uses the declarative JSON contract; it does not import a newer opaque `.cfg` into older firmware. Unexpected extra VLANs, port admin/speed/flow/EEE changes, or independent PVID drift without a VLAN repair stop automation. Loss of management also stops it. Automatic restoration cannot cover every possible factory-reset state.

A failed LACP negotiation with otherwise reachable LAN/QNAP is retained as a comparison result and allows the next stage. Loss of gateway and router SSH for about 30 seconds while QSS remains reachable triggers a controlled early return to 2.2.3 and baseline configuration. Router operations at stage boundaries, including immediately after reboot, receive bounded retries; an unavailable recorder also ends the comparison with a controlled return from a known firmware state. If QSS fails during these checks, the runner stops rather than issuing another upload.

SIGINT/SIGTERM and the `request-return` command request a return at a safe boundary. They do not kill an in-flight upload. A hard process kill or an ambiguous write requires inspection: `recover` refuses a journal with unresolved firmware/configuration write intent or an unconfirmed boot. It also refuses to overlap another coordinator. No automatic factory reset, address scan, password reset, or Firewalla bond cycling belongs to this workflow.

## Evidence and validation

`tools/lan_lacp_evidence.py` requires at least 300 seconds jointly clean native state and four fresh packet streams. It verifies the member mapping, aggregator, reciprocal actor/partner identities, and synchronization/collecting/distributing bits while rejecting defaulted/expired states. Packets expire after 35 seconds and native observations after 2.5 seconds. Every packet/native event is evaluated, including brief bad packets between polls. Frames from before the measured window cannot establish freshness. This proves sustained observed negotiation, not throughput or per-member data forwarding.

Per-stage files and journal observations are explicitly **provisional**. `result.json` is authoritative after recorder finalization. Missing final capture counters, kernel drops, or failed cleanup invalidate a provisional pass and are recorded as inconclusive. Raw remote captures remain available for later analysis if retrieval fails.

The 40-second passive smoke test completed with 39 native samples in the selected window, fresh packets from both interfaces, zero kernel drops, and no clean joint interval, consistent with the existing port-4 failure. Before/after QSS configurations matched, and no QSS POST occurred. The completed preflight also verified the independent port-6 management path, existing key-based sudo access, image hashes, baseline configuration, and reachable gateway.

Twenty-nine new tests cover exact raw byte delivery, ambiguous chunk acceptance, failed precheck, missing verification success, redirects, explicit arming, corrupt restoration image, controlled return and no return after ambiguous upload, VLAN ordering and full restoration/readback, unsupported drift, the management-path gate, router loss at stage boundaries, stale and pre-window packets, expired states, a brief bad PDU, reciprocal identity mismatch, and missing native samples. The broader suite initially passed 144 tests with seven loopback-server tests blocked by the sandbox; those seven passed when rerun with loopback access. Three subsequent targeted recovery tests also passed. Ruff checks and formatting passed. A separate agent reviewed the upload, configuration repair, recovery, and evidence logic, found the stage-boundary SSH recovery gap, and cleared the corrected implementation for the authorized trial.

## Local commands

The private prepared directory is `backups/firmware-unattended-prep-20260905/`. It contains the pinned `plan.json`, SSH material, read-only preflight evidence, and these executable local helpers:

- `start-firmware.command`: starts the authorized sequence in a new `live-run` directory; it refuses reuse of that directory.
- `status.command`: shows the latest durable status, without contacting the switch.
- `request-return.command`: asks the active runner to finish any upload, then return to the original firmware.
- `recover-firmware.command`: when the runner has stopped, checks the prior journal and restores 2.2.3/configuration only from a known safe state. An ambiguous upload is not replayed.

Each helper uses the already-installed local virtual environment and fixed plan digest. The live run directory contains `journal.jsonl`, `status.json`, `runner.log`, `recorder.json`, full snapshots/backups, captures, provisional results, and final `result.json`. Inspect the journal before manual recovery. A fresh execution should use a fresh snapshot, plan, and output directory rather than overwrite evidence.
