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

`/port_trunk_refresh.json` exposes a per-port value which the UI labels
"Link Up" or "Link Down". On 2026-09-05, port 4 returned down there while
the physical port header and port-settings page showed an active 2.5G link.
The UI language asset also describes the lowest-numbered member as the
group's mapped bridge port. Treat this field as opaque vendor status; it
has not been established as either physical carrier or per-member
collecting/distributing state. Use peer LACP evidence for protocol diagnosis.

Rendered-form audit (2026-09-05): the browser's `FormData` omits disabled
controls. With ports 1-4 configured for LACP and 5-10 disabled, it produces
23 fields; the controller's complete object contains 41. All shared values
match. The 18 controller-only fields are the priority, timeout, and retained
group of ports 5-10. Prior hardware checks validated read-back and persistence,
but the two payloads are not byte-for-byte equivalent, and side effects of
these extra disabled-port fields have not been excluded. No write was made
during this audit. See [the UI audit](qnap-ui-audit-20260905.md).

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

Hardware observation (2026-09-04): `tag_basedvlan.js` on the QSS 2.2.3 switch
builds exactly this `updatedVlans`/`deletedVlans` object. `vlan_id` and
`vlan_name` are strings, `port_states` is a 1-indexed integer array with a
reserved `0` at element zero, and the edited VLAN is always listed first. When
the edited VLAN claims a port untagged, the UI appends each conflicting VLAN
with that port set to `0`, unless that VLAN would become empty, in which case it
is omitted and left to the firmware. Editing VLAN 1 silently keeps any port
untagged in VLAN 1 if no other VLAN owns it untagged. Deletion is
`{"updatedVlans": [], "deletedVlans": [<id>]}` with an integer ID; the switch
silently ignores a string ID there (observed 2026-09-04) while `updatedVlans`
uses string IDs. The UI refuses to delete VLAN 1. The UI never sends a PVID write; it only blocks setting a port to
not-member in a VLAN equal to that port's PVID. Confirmed on hardware
2026-09-04: when a port's untagged membership moves between VLANs in one
`tag_vlan.json` POST, the switch changes that port's PVID by itself.

This project sends `deletedVlans` only from the gated `delete-vlan` command. It also refuses to take a port's
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

## Endpoint inventory

Every handler referenced by `myajax.js` on the QSS 2.2.3.20260713 UI, with the
HTTP method the UI uses. "Sampled" means the GET response was captured from a
physical QSW-L2110-10T on 2026-09-04 and the shape is reproduced by the
emulator. Anything marked write is out of scope for this project unless a row
above says otherwise; the last group is destructive.

### Used by this project

| Endpoint | Method | Notes |
|---|---|---|
| `get_model_name.json`, `status.json` | GET | identity guard |
| `port_trunk_cfg.json` | GET/POST | LAG configuration |
| `port_trunk_refresh.json` | GET | per-port link state |
| `tag_vlan.json` | SSE/POST | VLAN membership; stream ends with `{"done": true}` then EOF |
| `get_vlan_list.json`, `all_port_pvid.json` | GET | VLAN-ID list and PVIDs |
| `save_all_configs.json` | POST | persist running configuration |
| `config/download` | GET | opaque backup |

### Read-only, exposed by `dump-*` and `system-status` (sampled)

| Endpoint | Method | Notes |
|---|---|---|
| `port_vlan_cfg.json` | GET/POST | per-port `PVID` and `Frame_Type`; the hidden port-based VLAN page. Its POST is the only PVID setter found; untested |
| `port_setting_load.json` | GET | admin state, speed/duplex, flow control, EEE per port |
| `port_stats.json` | GET | compact link state per port (`connected`/`unconnected`) |
| `port_statistics.json` | GET | Tx/Rx good and bad packet counters per port |
| `mac_get_dynamic_mac_entries.json` | GET | `{"batch": [{mac_addr, vlan_id, fid, portid, age_timer}]}`; useful for isolation tests |
| `system_status.json` | GET | `fw_time` and `uptime`; useful for reboot-persistence checks |

### Read-only, sampled but not exposed

| Endpoint | Method | Notes |
|---|---|---|
| `tag_vlan_cfg.json` | SSE/POST | same stream as `tag_vlan.json`; POST is form-based |
| `stp.json` | GET/POST | `stp_enable`, `stp_rstp_mode`, per-port edge and state |
| `port_loop_status.json`, `port_lock_cfg.json` | GET(/POST) | loop detection violations and settings |
| `storm_ctrl_cfg.json` | GET/POST | per-port broadcast/multicast/unknown-unicast limits |
| `dhcp_snooping_cfg.json` | GET/POST | snooping mode and per-port trust/rate limit |
| `eee_config.json` | GET/POST | per-port EEE, keyed `Idx_0`..`Idx_7` for the 2.5G ports |
| `port_mirror.json` | GET/POST | monitoring port and per-port ingress/egress mirroring |
| `port_vlan.json` | GET/POST | hidden page's GET returns per-port PVID/frame type; read confirmed 2026-09-05; its rendered UI fails on missing translations |
| `acl_add.json` | GET/POST | hidden ACL page uses GET to list rules; read confirmed 2026-09-05, zero entries; POST remains a write |
| `qos_get_port_mode.json` | GET | `{"qos_mode": 0}` |
| `sntp_setting.json`, `systemtime_settings.json` | GET/POST | SNTP server, poll interval, time, zone, DST |
| `fid_vlan_map_cfg.json`, `lldp_loadsts.json` | GET/POST | return HTTP 400 on GET; the UI uses POST for LLDP |

### Not sampled

| Group | Endpoints |
|---|---|
| QoS | `qos_get_*`, `qos_set_*`, `qos_save_*` for rate limit, queue scheduling, CoS/DSCP/TC maps, port priority |
| IGMP snooping | `igmp_config`, `igmp_query_*`, `igmp_rp_*`, `igmp_get_entries`, `igmp_add_static_entries`, `igmp_delete_entries`, `igmp_save_entries` |
| LLDP | `lldp_enable`, `lldp_disable`, `lldp_fetch_rx_data`, `lldp_loadsts` (all POST) |
| MAC table writes | `mac_add_static_mac_entries`, `mac_save_static_mac_entries`, `mac_delete_static_mac_entries`, `mac_clear_static_mac_entries`, `mac_clear_dynamic_mac_entries` |
| ACL | `acl_add`, `acl_del`, `acl_save` (page hidden in the menu) |
| Port settings writes | `apply_user_port_setting`, `update_port_id_list`, `update_bpid_list`, `rx_auto_adapt`, `clear_statistics` |
| Port-based VLAN | `port_vlan.json`, `save_port_vlan_map`, `save_tag_vlan_map` |
| Diagnostics | `cable_test`, `eye_test_*`, `usxgmii_reg_rd`, `usxgmii_reg_wr`, `dynamic_tools_opt_check` |
| Management | `network_settings`, `network_settings_ipv4`, `network_settings_ipv6`, `user_ac_cfg` (password), `set_des`, `check_session_alive` |

### Destructive, never called by this project

`system_reboot.json`, `factory_reset.json`, `fwupdate_reboot_check.json`,
`cfgupdate_reboot_check.json`. A factory reset collapses every port into one
broadcast domain; see the fail-open warning in the topology notes.
