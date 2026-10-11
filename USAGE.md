# Using Chartroom

## Overview

This is **Chartroom** — a command-line tool. This document covers the surface area you can use, with copy-pasteable examples for each entry point. For setup, see `INSTALL.md`.

## Quickstart

```bash
this-tool --help
# or, if installed from source without symlink:
python -m Chartroom --help
```

## Commands

This CLI exposes **3** subcommands. Each was extracted from the source.

| Command | Source file |
|---------|-------------|
| `this-tool charts` | `chartroom.py` |
| `this-tool course` | `chartroom.py` |
| `this-tool tide` | `chartroom.py` |

### Per-command help

```bash
this-tool --help                # top-level help
this-tool charts --help    # help for `charts`
```

## Worked example

1. Verify the CLI is on your PATH: `which this-tool` (or run via `cargo run --`/`python -m`).
2. Show all subcommands: `this-tool --help`.
3. Try the first detected subcommand:

```bash
this-tool charts --help
```

## Reference

### Entry points

_No explicit entry points declared in this project's manifests._

### Surface index (auto-extracted)

| Kind | Title | Source file |
|------|-------|-------------|
| command | `charts` | `chartroom.py` |
| command | `course` | `chartroom.py` |
| command | `tide` | `chartroom.py` |

## Next steps

- Read [INSTALL.md](./INSTALL.md) if anything in the Quickstart didn't work.
- Inspect `.env.example` for the full list of environment variables this project understands.
- See [README.md](./README.md) for the architecture diagram + feature list.
