# Security policy

This project controls an infrastructure device through a private, unsupported
interface. Treat every issue as potentially network-affecting until reproduced
with the ONT and production LAN disconnected.

Please use GitHub private vulnerability reporting for security-sensitive findings.
Do not open a public issue containing:

- passwords or MD5 login digests;
- `session` or `user` cookies;
- public or management IP addresses;
- MAC addresses or serial/cloud keys;
- packet captures with identifying traffic;
- QSS configuration backup files.

There are currently no stable releases and no production-support promise. The
known replayable-authentication and private-API risks are documented in
[`docs/limitations.md`](docs/limitations.md).
