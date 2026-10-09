# Focus diagnostics (macOS)

Run on the main thread with the existing PyObjC Cocoa/Quartz environment:

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

There is no documented full validation gate. Native smoke testing only verifies
startup, bindings, run-loop operation and shutdown; it does not reproduce a real
Firefox focus incident or prove that all workspace notifications are delivered.
