# Contributing

Hardware observations and sanitized fixtures are especially useful while the
project is pre-alpha.

Before submitting a change:

```console
uv venv --python 3.14
uv sync --locked --extra dev
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
```

The full suite binds a loopback HTTP socket for the contract emulator. See
[pre-hardware testing](docs/pre-hardware-testing.md) for manual use and the clear
boundary between emulator evidence and hardware evidence.

When reporting a new model or firmware build, include:

- exact model and firmware version;
- whether each test was read-only or a write;
- sanitized response shape, with identifying values replaced;
- comparison against the QSS UI;
- reboot/read-back result for any write.

Never submit vendor firmware or extracted UI assets. Do not copy code from an
unlicensed repository. Interface facts, independently written tests, and original
implementations are welcome under this project's MIT license.
