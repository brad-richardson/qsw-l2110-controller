# Firmware comparison preparation — 2026-09-05

The images, comparison plan, and hardware-free rehearsal are prepared. **No firmware was uploaded, no Live Update operation was started, and no reboot was requested.** The switch remains on 2.2.3.20260713. The preparation tool exposes no hardware execution command.

The [Mac timeout experiment](macbook-timeout-results-20260905.md) established a useful Long-timeout control on ports 1+2. It also identified a plausible macOS receive-timer explanation for the Short failure. A firmware comparison tests a separate hypothesis; a simulated result cannot establish which firmware handles LACP correctly. Earlier Firewalla testing already failed on production ports 3+4 with Long.

All three official images were downloaded locally and verified against both the recorded SHA-256 and published MD5. Each is 5,407,908 bytes. Images remain in ignored `backups/firmware/`; vendor firmware/UI assets are not redistributed.

| Role | Exact firmware | Official release notes |
|---|---|---|
| Current baseline and final restoration | 2.2.3.20260713 | [2.2.3](https://www.qnap.com/en/release-notes/qss/2.2.3/20260713) |
| First comparison | 2.2.2.20260520 | [2.2.2](https://www.qnap.com/en-us/release-notes/qss/2.2.2/20260520) |
| Second comparison | 2.2.1.20260417 | [2.2.1](https://www.qnap.com/en-us/release-notes/qss/2.2.1/20260417) |

The machine-readable [image catalog](../examples/experiments/firmware-comparison.json) contains exact URLs, lengths, hashes, and the disabled execution flag. The existing [firmware notes](firmware-images.md) retain release-note differences. Those notes do not list an LACP change.

Static inspection of the current authenticated UI and all three image files found the same manual-upload JavaScript, byte for byte over the extracted upload functions: SHA-256 `1df4558af82e665cf096d6e5cbc300d4c382f2f269fd326d45e8de17a4d0a75d` (7,707 bytes). The observed UI workflow is:

1. POST `fwupdate_reboot_check.json`. Its backend effects are not established; it was not invoked.
2. POST sequential **raw file chunks** to `/firmware/upgrade`. HTTPS uses 12,000-byte chunks; HTTP uses 41,000-byte chunks. Although the HTML form declares multipart encoding, JavaScript sends raw blobs. There is no offset or idempotency token in these requests.
3. Examine the final response for a verification-success line. The UI waits 15 seconds and navigates to login. That delay alone is not proof of a successful reboot or correct firmware.

The UI retries a rejected fetch promise up to three attempts. A future automation must avoid blindly reproducing that behavior: after an ambiguous response, the receiver may already have consumed the chunk. Replaying it could corrupt the upload stream. The rehearsal stops and records this uncertainty.

Live Update uses POST `/api/ota/check`, POST `/api/ota/start`, and GET `/api/ota/status`. The inspected UI exposes no target-version or image-URL selector, so its known success on this unit does not establish a way to select the two older images. The user clarified that the previous manual attempt occurred through the UI and may have been interrupted; its failure remains unexplained. None of these OTA endpoints was called during preparation.

Run the preparation locally from the repository root:

```sh
.venv/bin/python -m tools.firmware_lab verify-images --images backups/firmware
.venv/bin/python -m tools.firmware_lab snapshot --env-file .env --insecure \
  --output backups/firmware-ui-prep/fresh-snapshot.json
.venv/bin/python -m tools.firmware_lab prepare \
  --snapshot backups/firmware-ui-prep/fresh-snapshot.json \
  --images backups/firmware --output backups/firmware-ui-prep/comparison-plan-new.json
.venv/bin/python -m tools.firmware_lab rehearse \
  --plan backups/firmware-ui-prep/comparison-plan-new.json \
  --images backups/firmware --output backups/firmware-ui-prep/rehearsal-new.json
```

Output files must be new, preserving previous evidence. `prepare` checks the model, starting firmware, image hashes, and existing group 1. It constructs a **local candidate** with Long on test ports 1+2, preserving all production fields, VLANs, PVIDs, and port settings. It records a separate hash of the source snapshot and lists any live readiness gaps. It changes no switch settings. Production Long scope remains a separate user decision.

The successful rehearsal fed the real verified image bytes to an in-memory receiver: 451 chunks per image, 1,353 total, in the requested version order including restoration. Its firmware versions, reboot events, configuration responses, and LACP observations are explicitly synthetic. It is not a CPU/ASIC emulator and does not execute QNAP firmware.

Thirteen targeted tests cover image corruption before any upload, wrong identity, preservation of production fields, exact byte delivery without network access, stopping on ambiguous chunk acceptance, rejected image, reboot timeout, wrong returned build, changed configuration, literal credential loading, and protected read-only snapshot/retry behavior. No unsafe case advanced to the next firmware or attempted blind restoration. Synthetic stale-packet and native-expired observer cases were also rehearsed; their failed negotiation results were retained while the comparison continued. These cases exercise result handling, not a physical packet/native-state evaluator. [Preparation evidence](evidence/firmware-preparation-20260905-summary.json) records the outcomes.

`snapshot` reads credentials literally from `.env`, with `QSW_*` environment overrides, and authenticates over HTTPS. It makes only GET requests, including the opaque configuration backup, and writes both files with mode 0600. It holds a local lock for the entire authenticated read sequence and retries read failures up to three times with fresh sessions. `--insecure` permits this switch's local certificate; omit it when a trusted certificate is installed. The lock coordinates this tool's processes, not an independently opened browser or another tool.

For the eventual physical experiment, keep the same cables and Long setting throughout and use a local runner that survives loss of the agent connection. Record a fresh 450-second baseline on 2.2.3, then each older build, then 2.2.3 again. Start raw capture and one-second native snapshots before the first upload and keep recording across switch reboots. Start each measured window only after exact firmware identity, unchanged configuration, both physical links, and fresh reciprocal LACPDUs are verified. Require five minutes of jointly clean native/packet evidence to call a negotiation pass. Preserve a failed negotiation result and compare the next build only if management and production configuration remain healthy.

A live upload driver and privileged Mac capture lifecycle are **not exposed by this preparation tool**. Wiring them to the prepared state sequence requires a separate execution change. That change must serialize all QSS reads, authentication, upload, and watchdog checks under one coordinator. The timeout trial's concurrent observer/disarm reads returned malformed JSON; every network consumer must respect the same serialization, and other QSS UI polling should be paused during uploads. Read-only errors may be retried with fresh authentication and bounded backoff; firmware POSTs must not be replayed after ambiguity.

A real firmware change necessarily interrupts switching and therefore this Wi-Fi path's internet reachability during reboot. Hands-free recovery is conditional on management address, credentials, VLANs, and configuration surviving. If the device rejects a downgrade, comes back at another address, resets configuration, or remains unreachable, stop and preserve evidence. Do not automatically import a newer `.cfg` into an older build or start another flash. Preserve a current backup and a recovery path independent of the tested LAG; keep the ONT directly connected to Firewalla. A local journal can survive an internet interruption but cannot guarantee recovery from an inaccessible switch.

Before enabling a live run, refresh the complete baseline after the chosen Long change, confirm its scope, integrate and validate the real capture driver, and review the upload acceptance/recovery behavior. The downloaded 2.2.3 image is available for a planned return after successful comparisons; it is not an unconditional watchdog rollback action. No factory reset belongs in this comparison, because that would change another variable.
