# Focus diagnostics (macOS)

## Setup and development

The monitor requires macOS and PyObjC Cocoa/Quartz. Fresh development environments
use Python 3.14; install `uv`, Git, and Make first, then run:

```sh
make setup
```

When `venv` is absent, setup runs `uv venv venv --python 3.14 --seed`, then
`venv/bin/python -m pip install -r requirements.txt`, then installs both Git
hook types using `venv/bin/python -m pre_commit install --hook-type pre-commit
--hook-type commit-msg`. An existing Python 3.14 virtual environment is reused;
a different version, broken environment, or non-venv destination is rejected
**before any install or replacement**. Setup never deletes an existing `venv`.
If you have a conflicting environment serving a running monitor, leave it intact
and use a separate fresh checkout for 3.14. Migrate only when you can safely
stop/restart the monitor yourself.

Requirements pin PyObjC core/Cocoa/Quartz 12.2.2, pre-commit 4.6.0, and Ruff
0.15.12. Transitive dependencies are resolved by pip (this is not a fully locked
package set). Setup may download Python and seeded pip via uv, and Python
packages via pip. The first pre-commit run downloads/builds the official
Gitleaks v8.30.1 Go hook and may provision a Go toolchain; network access is
needed for these downloads. No pytest, type checker, or coverage gate is required.

To add hooks independently to an existing usable environment without reinstalling
its runtime packages:

```sh
venv/bin/python -m pip install pre-commit==4.6.0 ruff==0.15.12
make install-hooks
```

This path does not enforce the fresh-setup Python version. It does not replace
an existing environment or alter Git's `core.hooksPath`; if a custom hook path
prevents installation, resolve it yourself before retrying. Missing bootstrap
tools fail visibly instead of skipping required checks.

Direnv is optional. If you use it, inspect your local `.envrc` yourself and only
then decide whether to run `direnv allow`. Setup does not install direnv, read or
write `.envrc`, or approve it. Activation is not required by Make targets.

### Commands and hooks

| Target | Purpose |
| --- | --- |
| `make setup` | Safe 3.14 environment setup, requirements, both hooks |
| `make install-hooks` | Install pre-commit and commit-msg into this checkout |
| `make test` | Deterministic unittest suite, no native monitor |
| `make lint` | Ruff lint and import checks |
| `make format` | Apply Ruff formatting |
| `make format-check` | Check formatting without edits |
| `make check` | Lint, format-check, and tests |
| `make run` | Start the monitor, writing its private default log |
| `make help` | List targets |

The pre-commit stage separately gates Ruff lint, formatting, and unittest using
this checkout's `venv`, plus Gitleaks scanning the staged Git changes with
redacted output. The Python gates run over the whole working tree, even for
non-Python commits; they never auto-format or skip failures. `make check` covers
the same Python checks but does not scan secrets. To explicitly check staged
changes including secrets, run:

```sh
venv/bin/python -m pre_commit run --hook-stage pre-commit --all-files
```

Gitleaks still scans **staged** changes, not every untracked/unstaged file, even
with `--all-files`. Ignored private logs must never be force-added. Fix failed
checks before committing; do not bypass hooks.

The separate commit-msg stage requires a Conventional Commit subject with a
nonempty description. Valid examples include `feat: add output`,
`feat(monitor): add output`, `feat(): add output`, and `fix(monitor)!: change output`.
Allowed types: build, chore, ci, docs, feat, fix, perf, refactor, revert, style,
and test. Empty messages, malformed subjects, and whitespace-only descriptions
are rejected; merge/release messages have no exemption. Use ordinary project
language in public artifacts, not internal tracking labels or local host details.

## Running the monitor

Run on the main thread with the PyObjC Cocoa/Quartz environment:

```sh
venv/bin/python main.py
venv/bin/python main.py --log /path/to/focus-events.jsonl
venv/bin/python main.py --output json
venv/bin/python main.py --output jsonl > /path/to/stdout-events.jsonl
```

The default is `focus-events.jsonl` beside `main.py`. Parent directories must
already exist. Logs are appended, never truncated; symlinks and non-regular
files are rejected. New files are owner-only (0600); existing file permissions
are preserved. Each JSONL record is flushed immediately (not fsynced). Stdout
shows readable timestamped events by default (`--output text`). Ctrl-C removes
observers and closes the file. The existing monitor must be restarted manually
to use this code.

`--output json` prints each complete event record as an indented JSON object.
These are **sequential event objects**, not one JSON document or a session array;
use `--output jsonl` for parsers. JSON syntax is colored only when stdout is a
TTY (including a PTY), never for pipes or redirected files. Set `NO_COLOR`
(even an empty value) or `TERM=dumb` to disable coloring.

`--output jsonl` emits one compact, complete JSON record per line, always without
ANSI color, including on a TTY. Both JSON modes contain only event records on
stdout (no timestamp prefixes or banners). Errors remain on stderr; `--help`
prints conventional CLI help without starting the monitor. The persistent
logfile stays plain, immediately flushed JSONL with the same complete records
regardless of stdout mode.

### Optional YAML conversion

For YAML, use the optional external converter nesdit:
https://github.com/sdkks/nesdit

```sh
venv/bin/python main.py --output jsonl |
  nesdit --format jsonl --output-format yaml --query '.'
```

This produces a stream of YAML documents separated by `---`. Use JSONL input,
not pretty JSON, for streaming conversion. nesdit is not a project dependency;
install it separately using its public repository's instructions. Conversion
still exposes the same application-usage data, so keep its output private too.

The monitor takes an initial frontmost-app snapshot and observes activation,
deactivation, launch, termination, hide, unhide, and active-Space changes through
NSWorkspace notifications on the main-thread run loop. It records app name, PID,
bundle identifier, app version/build when available, and OS metadata at startup.
Missing app/version metadata is `null`; a Space event does not identify a Space.

`observed_at` is UTC local observation time; `monotonic_seconds` measures local
receipt timing, **not exact originating event time**. Focus durations begin at
an observed activation (or the initial snapshot) and end at deactivation receipt
if observed, otherwise the next activation receipt. The initial duration cannot
include focus time before startup. Duplicate activations do not reset duration.
`rapid_return` marks A → B → A when the return is observed within 2 seconds of
B's activation. These are diagnostic heuristics, not evidence of focus stealing
or trigger attribution; notifications may be delayed or missing.

Each activation and initial snapshot includes Quartz elapsed ages for keyboard
(key down/up and modifier changes) and mouse-button events (not movement/scroll).
`reported_unverified` explicitly means availability/permissions are uncertain:
a returned zero is not proof of input, a large age is not proof of no input, and
no age identifies a cause. Invalid values or API errors yield `unavailable` and
`null`, not a guessed absence of input. No permissions are requested or changed.

No keystroke contents, event taps, window titles, Accessibility monitoring, or
app activation are used. Logs still reveal application usage and timing: keep
them private, review before sharing, and manage retention yourself. There is no
rotation. Schema version 1 records include the event, observation timestamps,
timing basis, and event-specific context; normal Ctrl-C runs have session start/stop
records. Startup or I/O failures report an error and may leave a partial session.

Focused deterministic checks (no global input or app switching):

```sh
venv/bin/python -B -m unittest -v test_main
```

`make check` is the full Python validation gate; the installed pre-commit hook
also scans staged changes for secrets. Native smoke testing only verifies
startup, bindings, run-loop operation and shutdown; it does not reproduce a real
Firefox focus incident or prove that all workspace notifications are delivered.
