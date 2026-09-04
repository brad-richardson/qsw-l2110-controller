# Private QSS 2.2.x interface notes

These notes describe observed implementation facts, not a supported QNAP API.
They were derived from static inspection of the official QSW-L2110 firmware:

- Version: QSS 2.2.3 build 20260713
- Image SHA-256: `4c9282b57e6d497623a6700c65c5a75bb33b19e504bf813155a2ae0ccb19b12b`
- Runtime clues: Zephyr paths, CivetWeb, ports 80 and 443

The firmware image and vendor UI assets are not redistributed by this project.

## Authentication

The UI computes MD5 digests and performs:

```text
GET /authorize?loginusr=<md5(username)>&loginpwd=<md5(password)>
```

The response establishes `session` and `user` cookies. Treat both digests and
cookies as secrets. Use HTTPS and never log the authorization URL.

Hardware observations (QSW-L2110-10T, 2026-09-04):

- The `/authorize` response carries neither `Content-Length` nor
  `Connection: close`, and the switch then drops the socket. An HTTP client
  that pools the connection fails its next request with a protocol error
  (h11: `ConnectionClosed` in state `SEND_RESPONSE`). The client sends
  `Connection: close` on every request and retries read-only GETs once.
- Unauthenticated JSON handlers return `{"redirect": ".../login.html"}` with
  the entire status line, headers, and body duplicated inside the body. Parse
  only the first JSON object if this ever needs to be read directly.
- Element zero of the SSE `port_pvids` array in `/tag_vlan.json` is
  uninitialized memory (observed `1879053478`). PVIDs must be read only from
  `/all_port_pvid.json`, whose element zero is a real `0`.
- `/config/download` returns exactly the file the web UI offers. On a
  factory-fresh switch it is 159 bytes and holds only non-default keys
  (`sys/sntp/server/ip`, `sys/qos/mxl_qos_cos`, `sys/usr_pwd`). The backup
  embeds the admin password hash, so treat it as a secret.

## Identity and system status

```text
GET /get_model_name.json
GET /status.json
```

Known response fields include `model_name`, `des`, `fw_ver`, `hw_ver`,
`sys_ipv4`, `sys_macaddr`, and `temperature`.

The controller requires an exact configured `fw_ver` match. A private interface
must not be assumed compatible merely because its major/minor prefix is unchanged.

## LAG configuration

```text
GET  /port_trunk_cfg.json
POST /port_trunk_cfg.json
GET  /port_trunk_refresh.json
```

The GET response is nested:

```json
{
  "PortNum": 10,
  "system_priority": "32768",
  "Port_1": {
    "portTypeId_1": "2",
    "portPriorityId_1": "128",
    "lacpTimeoutId_1": "0",
    "Port_1_grpInd": "1",
    "Port_1_state": 1
  }
}
```

The UI POST is a flat object containing `system_priority` and the form fields
for physical ports. Observed values:

| Field | Values |
|---|---|
| `portTypeId_N` | `0` disabled, `1` static, `2` LACP |
| `portPriorityId_N` | 1-65535, default 128 |
| `lacpTimeoutId_N` | `0` short, `1` long |
| `Port_N_grpInd` | group 1-10 |
| `system_priority` | 0-65535, default 32768 |

Hardware observation (2026-09-04): the UI script `port_trunking.js` builds the
trunk-group dropdown with option values equal to the group number 1-10, and
`form_to_json()` in `myajax.js` serializes the whole form as a flat JSON object
of string values, so `Port_N_grpInd` is a real group ID and the POST is flat as
assumed. `save_all_configs.json` is a separate POST with no body.

The client requires every known field and sends a complete ten-port object
derived from the current GET, then overlays only explicitly managed ports. It
refuses a desired group ID already used by an unmanaged active port. Unknown
future fields cannot be preserved in the UI's flat POST schema. It also refuses
to move a managed port away from a current group containing unmanaged peers.

`/port_trunk_refresh.json` exposes raw per-port link up/down state. Static UI
inspection does not show collecting/distributing LACP protocol state.

## VLAN configuration

```text
GET  /tag_vlan.json          # Server-Sent Events
POST /tag_vlan.json
GET  /get_vlan_list.json
GET  /all_port_pvid.json
```

The SSE stream first reports `PortNum`, then one event per VLAN. A VLAN entry uses
a 1-indexed state array; element zero is reserved:

```json
{
  "vlan_id": "3999",
  "vlan_name": "wan-transit",
  "port_states": [0, 1, 1, 0, 0, 0, 0, 0, 0, 1, 0]
}
```

State values are `0` not-member, `1` untagged, and `2` tagged. The write payload
supports partial updates and explicit deletions:

```json
{
  "updatedVlans": [
    {
      "vlan_id": "3999",
      "vlan_name": "wan-transit",
      "port_states": [0, 1, 1, 0, 0, 0, 0, 0, 0, 1, 0]
    }
  ],
  "deletedVlans": []
}
```

This project never populates `deletedVlans`. It also refuses to take a port's
untagged membership from a VLAN omitted from the desired file.

The separate inventory helpers return shapes inferred from their UI consumers:

```json
{"vlan_ids": [1, 10, 3999]}
{"port_pvids": [0, 3999, 3999, 10, 10, 10, 1, 1, 1, 3999, 10]}
```

The PVID array is 1-indexed with reserved element zero. A safe snapshot brackets
the finite SSE read with VLAN-ID and PVID reads, requires stable before/after
values, unique identical VLAN-ID sets, and a clean EOF. A timeout is never
accepted as completion.

For an untagged ownership move, update entries are ordered with the destination
VLAN before the old source VLAN, matching the direction observed in the UI. The
UI normally submits one active destination plus conflicting sources; multi-
destination batching remains hardware-gated. PVID writes through
`/port_vlan.json` are not implemented. Apply requires the resulting PVIDs to
match desired untagged ownership before save and again afterward.

## Persistence and backup

```text
POST /save_all_configs.json
GET  /config/download
POST /config/upload
```

Configuration upload is deliberately not wrapped. It replaces configuration and
may reboot the switch. `apply` uses download only, performs read-back checks after
writes, calls the global save handler, and verifies the running configuration
again. Only a hardware reboot test can establish flash persistence. Automatic
backups use exclusive mode-`0600` creation and include a displayed SHA-256 checksum.

`QswL2110Client` is a low-level transport for protocol investigation. Its direct
mutating methods do not invoke the CLI's identity, backup, planning, ordering, or
read-back gates and are deliberately not exported from the package's top level.

## Other discovered handlers

The firmware includes handlers for port statistics/settings, mirroring, EEE,
QoS, IGMP snooping, loop protection, DHCP snooping, MAC tables, network settings,
time/SNTP, firmware update, reboot, and factory reset. They are out of scope until
the core identity/LAG/VLAN behavior is validated on hardware.
