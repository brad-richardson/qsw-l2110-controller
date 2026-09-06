# Native diagnostics and console investigation — September 6, 2026

Offline review of saved QSS JavaScript and the exact firmware image
`QSW-L2110-FW.v2.2.3_S20260713_100043.img`, SHA-256
`4c9282b57e6d497623a6700c65c5a75bb33b19e504bf813155a2ae0ccb19b12b`.
No diagnostic endpoint, register read/write, TDR, eye test, console connection,
or switch login was performed during this review. Live availability remains
unverified. The manual-UI sweep still opens no QNAP session.

## What the shipped UI actually does

| Endpoint | Native operation | Established scope / limitation |
| --- | --- | --- |
| `dynamic_tools_opt_check.json` | GET; passes response to menu builder | Adds the eye-test link only if `phy_eye_test == "true"`. This is a feature-visibility check, not a console command or permission grant. |
| `cable_test.json` | GET reads results; POST submits selected ports | Cable diagnostics are commented out of the menu. Reading cached results is distinct from starting a new test. |
| `usxgmii_reg_rd.json` | POST with `port`, `reg_sel`, `reg_addr`; response `rd_reg_data_out` | UI selects USXGMII interface **0 or 1**, and XPCS (0) versus PHY (1) register space. This does not establish per-front-panel-port MAC counters or LACP receive visibility. |
| `usxgmii_reg_wr.json` | POST adds `data_in` | Register write, excluded from the proposed availability/read inspection. |
| `eye_test_fpeye_start.json`, `eye_test_scope_eye_start.json` | POST starts tests | Parameters include loopback, BER/error controls, debug and scope mode. These are active diagnostics, not passive status reads. |
| Other `eye_test_*` endpoints | POST status/result/data requests | Their names alone do not establish harmlessness, availability or exact state changes. |
| `rx_auto_adapt.json` | POST for selected USXGMII interface | An adaptation action, not a register/status read. |

The eye page's actual radio controls offer only `PORT_0` and `PORT_1`. The
JavaScript comments independently say “usxgmii port 0” and “usxgmii port 1”.
Mapping these interfaces to front-panel ports or shared internal paths is still
unknown. Do not pass front-panel port 4 as this endpoint's `port` parameter.
No exact XPCS/PHY register map, known-safe register allowlist, or counter-clear
semantics has been established for this build.

The cable page presents four copper-pair results per port: OK, Open, Intra-pair
short, Inter-pair short, Pair Busy, Unknown, or Fail, with length/distance fields.
Its source comments limit cable diagnostics to eight ports; the display uses
the returned `PortNum`. The UI polls results every second with a 40-second UI
window. That timer is not proof of test completion or maximum link disruption.

Passing TDR would reduce suspicion of gross copper-pair faults under the test
conditions. It would **not settle all physical-damage hypotheses**, nor test
packet classification, LACP processing, or every MAC/PHY/SERDES function.
Test the same cable/end condition on a good and failing port if this is pursued.
Treat starting TDR as a separate potentially link-disrupting experiment: any
recovery could result from the interruption itself.

## Stronger evidence for a serial shell

The exact image contains:

- `WEST_TOPDIR/zephyr/subsys/shell/backends/shell_uart.c`
- `shell.shell_uart`, `shell_uart_backend`, `shell_uart`
- `UART_0`
- `uart:~$ `

This adds an identifiable UART backend to the previously found `lacp status`,
`trunk`, and LAG mapping diagnostics. It strengthens the serial-console lead;
it does not prove the backend is active on the retail board or accessible without
authentication. Connector location, electrical interface, pinout, serial settings,
and production enablement remain unknown. A Zephyr diagnostic shell also does
not imply a Linux root shell or SSH server.

QNAP's own [QSW-L2110-10T hardware specification](https://www.qnap.com/en-us/product/qsw-l2110-10t/specs/hardware)
lists `Console: RJ45`. This needs reconciliation with the actual chassis and
supported access instructions before selecting a cable or using an Ethernet jack
as a console. No connector/pinout was identified in this review.
Zephyr documents its [UART shell backend](https://docs.zephyrproject.org/latest/services/shell/index.html),
but generic Zephyr defaults are not verified QNAP settings.

## Next bounded checks

1. With the single QSS session available, verify switch identity, GET the dynamic
   feature flags, and GET existing cable-test results. Record unsupported/error
   responses as availability evidence; do not retry with a start/write operation.
2. Establish the USXGMII interface mapping and exact register semantics before
   issuing register reads. Reads may acknowledge/clear status on some hardware;
   “read” is not a blanket guarantee that an arbitrary address is observation-only.
3. Ask QNAP how to access the UART backend on this A0 retail unit, including the
   advertised RJ45 console location, pinout/electrical standard, serial parameters,
   credentials and whether a diagnostic build is necessary.
4. If console access is available, request supported observational commands for
   LACP member RX/validation counts and CTP/BP/Pmapper state. Those are closer to
   the suspected receive/membership failure than an unspecified SERDES register.

Keep all of these checks separate from the clean, UI-only six-pair baseline.
No automatic diagnostic polling is being added to the manual-UI observer.
