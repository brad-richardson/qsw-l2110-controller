# QSS firmware images for the QSW-L2110

QNAP's Download Center for the QSW-L2110-10T shows only the newest build until
"All operating system versions" is clicked. The page is JavaScript-rendered; plain
HTTP fetches of `www.qnap.com` return empty bodies, but the image files on
`download.qnap.com` download normally.

Images are not redistributed by this project. Keep local copies under the ignored
`backups/firmware/` directory and verify them against this table before use. All three
files are exactly 5,407,908 bytes.

| QSS version | Build | Published | File on `download.qnap.com/Storage/Networking/QSW-L2110/` | MD5 | SHA-256 |
|---|---|---|---|---|---|
| 2.2.1 | 20260417 | 2026-05-12 | `QSW-L2110-FW.v2.2.1_S20260417_100035.img` | `6c262e6e6199300e1fadeacede29ab7b` | `d11bcd0d8e9abbeb7f1c0f78ec949aabb6741a68056c4c87c3bf006357e8c6f5` |
| 2.2.2 | 20260520 | 2026-06-02 | `QSW-L2110-FW.v2.2.2_S20260520_100037.img` | `b19fde2d80ec3c6c413e3442ac15d624` | `224c102c88e754eb06140bcd28f45a9b8d8452300e5caee41b5b7fe24bef531b` |
| 2.2.3 | 20260713 | 2026-07-31 | `QSW-L2110-FW.v2.2.3_S20260713_100043.img` | `33da6856d1327f0a65d66df40f347c49` | `4c9282b57e6d497623a6700c65c5a75bb33b19e504bf813155a2ae0ccb19b12b` |

MD5 values are the ones QNAP publishes next to each download; SHA-256 values were
computed locally on 2026-09-05. The 2.2.3 SHA-256 matches the image already recorded
in [api-notes.md](api-notes.md).

Release-note deltas, from QNAP's pages:

- 2.2.1 to 2.2.2: SNTP enabled by default; a QoS rate-limit checkbox fix.
- 2.2.2 to 2.2.3: web interface unreachable after long HTTPS sessions; a password
  security fix; "a port could learn MAC addresses from an unassigned VLAN"; a Qfinder
  display fix; better Live Update error messages. No LACP change is listed.

Downgrade notes:

- Firmware flashing is deliberately outside this controller. Use the QSS web UI's
  firmware page; whether QSS accepts an older image through manual upload is not
  documented. The 2.2.3 manual upload failed once on this unit and Live Update
  succeeded. The user later clarified that the manual attempt was through the UI
  and may have been interrupted; its failure remains unexplained.
- Keep the ONT off the switch until the configuration is re-applied and verified;
  a downgrade may reset the switch to one flat VLAN.
- Add the target build to `device.firmware` in the YAML before the controller will
  talk to it, and run the read-only commands first: endpoint shapes were sampled on
  2.2.3 only. Do not rely on importing a 2.2.3 backup into an older build.

The [firmware comparison preparation](firmware-comparison-preparation-20260905.md)
adds a pinned image catalog and hardware-free rehearsal. It inspects the raw
chunk upload protocol but exposes no live firmware execution command.

To re-fetch and verify:

```console
mkdir -p -m 700 backups/firmware && cd backups/firmware
curl -O https://download.qnap.com/Storage/Networking/QSW-L2110/QSW-L2110-FW.v2.2.2_S20260520_100037.img
sha256sum QSW-L2110-FW.v2.2.2_S20260520_100037.img
```
