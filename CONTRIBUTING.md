# Contributing

Hardware observations and sanitized fixtures are especially useful while the
project is pre-alpha.

Before submitting a change:

```console
uv sync --extra dev
uv run ruff check .
uv run pytest -q
```

When reporting a new model or firmware build, include:

- exact model and firmware version;
- whether each test was read-only or a write;
- sanitized response shape, with identifying values replaced;
- comparison against the QSS UI;
- reboot/read-back result for any write.

Never submit vendor firmware or extracted UI assets. Do not copy code from an
unlicensed repository. Interface facts, independently written tests, and original
implementations are welcome under this project's MIT license.
