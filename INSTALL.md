# Installing Chartroom

Chartroom is a single Python file. The program itself has **no dependencies** - the
step below exists only to install the pinned test runner (`pytest`) so the shipped
suite can run.

## Prerequisites

| Requirement | Notes |
| --- | --- |
| Python 3.8 or newer | `python3 --version`. Tested on CPython 3.11. That is the whole list: no compiler, no system libraries, no database, no Node, no browser. |

Nothing else is installed system-wide. `install.sh` never uses `sudo`, `apt-get`,
`apk`, `brew` or any other system package manager.

## Package manager

**pip**, driven by `requirements.txt` at the project root. That file is the complete,
pinned dependency list (`pytest==8.4.2`); it is also the lock for the test runner.

## 1. Install the (test-only) dependencies

```bash
bash install.sh
```

The script upgrades pip/setuptools/wheel, installs `requirements.txt`, self-checks that
`chartroom.py` imports cleanly and that `--help` answers, then **exits**. It never
starts a server or a long-running process.

Equivalent manual command if you prefer:

```bash
python3 -m pip install -r requirements.txt
```

## 2. Seed demo data (optional)

Chartroom keeps *your* records, so by default it starts empty - the empty states tell you
the exact command to run next. To see every screen populated instead, use the bundled
sample store (a copy, so the shipped file stays pristine):

```bash
mkdir -p ~/.chartroom
cp examples/sample-store.json ~/.chartroom/store.json
python3 chartroom.py charts list
python3 chartroom.py course list
python3 chartroom.py tide list
```

Or run the scripted offline walkthrough, which builds its own throwaway store and writes
the transcript to `demo_output.txt`:

```bash
bash demo.sh
```

## 3. Run it

There is no server to start. Invoke the command you need:

```bash
python3 chartroom.py --help
python3 chartroom.py charts add --number 1234 --title 'Solent Approach' --region Solent --scale 25000
python3 chartroom.py charts list
```

Useful environment variables (all optional): `CHARTROOM_STORE` to point at a store file
other than `~/.chartroom/store.json`, and `NO_COLOR` to switch off ANSI colour. See
`.env.example`.

## 4. Verify

```bash
python3 -m pytest
```

The suite runs entirely offline against temporary stores - no network, no fixtures to
download, no interactive prompts.

## Where things live

```
chartroom.py                the whole program (standard library only)
demo.sh                     offline end-to-end walkthrough, writes demo_output.txt
examples/sample-store.json  realistic store for populated screens
tests/                      pytest suite (charts/courses, tides, output contract, demo)
requirements.txt            pinned test runner (the program needs no dependencies)
install.sh                  pip install -r requirements.txt, then exits
```
