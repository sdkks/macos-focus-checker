"""Read-only macOS application focus diagnostics (no event taps or AX access)."""

import argparse
import json
import math
import os
import platform
import re
import signal
import stat
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

RAPID_RETURN_SECONDS = 2.0
DEFAULT_LOG = Path(__file__).resolve().with_name("focus-events.jsonl")


def open_log(path):
    """Append, never truncate; reject symlinks and non-regular destinations."""
    fd = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600
    )
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("log destination must be a regular file")
        stream = os.fdopen(fd, "a", encoding="utf-8")
    except BaseException:
        os.close(fd)
        raise
    return stream


# Tokenize only serialized JSON: metadata control characters are already escaped.
JSON_TOKENS = re.compile(
    r'"(?:\\.|[^"\\])*"|true|false|null|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|[{}\[\],:]'
)


def color_json(serialized):
    def highlight(match):
        token = match.group()
        if token.startswith('"'):
            is_key = serialized[match.end() :].lstrip().startswith(":")
            color = "36" if is_key else "32"
        elif token in ("true", "false", "null"):
            color = "35"
        elif token[0] in "-0123456789":
            color = "33"
        else:
            color = "37"
        return f"\033[{color}m{token}\033[0m"

    return JSON_TOKENS.sub(highlight, serialized)


class EventLog:
    def __init__(self, stream, output="text"):
        self.stream = stream
        self.output = output

    def emit(self, event, received, **fields):
        record = dict(
            schema_version=1,
            event=event,
            observed_at=datetime.now(timezone.utc).isoformat(),
            monotonic_seconds=received,
            timing_basis="local observation/notification receipt",
            **fields,
        )
        line = json.dumps(record, ensure_ascii=True, allow_nan=False)
        self.stream.write(line + "\n")
        self.stream.flush()
        if self.output == "json":
            rendered = json.dumps(record, ensure_ascii=True, allow_nan=False, indent=2)
            if (
                sys.stdout.isatty()
                and "NO_COLOR" not in os.environ
                and os.environ.get("TERM") != "dumb"
            ):
                rendered = color_json(rendered)
        elif self.output == "jsonl":
            rendered = json.dumps(record, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
        else:
            rendered = f"[{record['observed_at']}] {event}: " + json.dumps(
                fields, ensure_ascii=True, allow_nan=False
            )
        print(rendered, flush=True)


def input_ages(quartz):
    groups = {
        "keyboard": ("kCGEventKeyDown", "kCGEventKeyUp", "kCGEventFlagsChanged"),
        "mouse_button": (
            "kCGEventLeftMouseDown",
            "kCGEventLeftMouseUp",
            "kCGEventRightMouseDown",
            "kCGEventRightMouseUp",
            "kCGEventOtherMouseDown",
            "kCGEventOtherMouseUp",
        ),
    }
    result = {}
    for group, names in groups.items():
        try:
            ages = [
                float(
                    quartz.CGEventSourceSecondsSinceLastEventType(
                        quartz.kCGEventSourceStateCombinedSessionState, getattr(quartz, name)
                    )
                )
                for name in names
            ]
            if any(not math.isfinite(age) or age < 0 for age in ages):
                raise ValueError("invalid elapsed time")
            result[group] = {"age_seconds": min(ages), "status": "reported_unverified"}
        except Exception as exc:
            result[group] = {
                "age_seconds": None,
                "status": "unavailable",
                "error_type": type(exc).__name__,
            }
    result["caveat"] = (
        "Quartz elapsed times are context only; availability/permissions "
        "are not verified. Zero is not proof of input, and no age "
        "identifies a trigger or proves absence of input."
    )
    return result


def app_metadata(app, bundle_class):
    if app is None:
        return None
    version = build = None
    try:
        url = app.bundleURL()
        bundle = bundle_class.bundleWithURL_(url) if url else None
        if bundle:
            version = bundle.objectForInfoDictionaryKey_("CFBundleShortVersionString")
            build = bundle.objectForInfoDictionaryKey_("CFBundleVersion")
    except Exception:
        pass  # Missing bundle metadata is represented explicitly, not guessed.
    return {
        "pid": int(app.processIdentifier()),
        "name": app.localizedName(),
        "bundle_id": app.bundleIdentifier(),
        "version": version,
        "build": build,
    }


class FocusTracker:
    def __init__(self, log, get_input):
        self.log = log
        self.get_input = get_input
        self.current = self.previous = None
        self.started = self.ended = None

    def observe(self, event, app, received):
        fields = {"app": app}
        if event in ("initial_focus", "activation"):
            fields["recent_input"] = self.get_input()
            duplicate = (
                app is not None
                and self.current is not None
                and self.ended is None
                and app["pid"] == self.current["pid"]
            )
            fields.update(
                duplicate=duplicate,
                previous_app=self.current,
                previous_focus_seconds=None,
                rapid_return=False,
            )
            if app is not None and not duplicate:
                if self.current is not None:
                    end = self.ended if self.ended is not None else received
                    fields["previous_focus_seconds"] = max(0.0, end - self.started)
                    fields["rapid_return"] = (
                        self.previous is not None
                        and app["pid"] == self.previous["pid"]
                        and app["pid"] != self.current["pid"]
                        and received - self.started <= RAPID_RETURN_SECONDS
                    )
                self.previous, self.current = self.current, app
                self.started, self.ended = received, None
        elif event == "deactivation":
            if app is not None and self.current is not None and app["pid"] == self.current["pid"]:
                if self.ended is None:
                    self.ended = received
                fields["focus_seconds"] = max(0.0, self.ended - self.started)
        self.log.emit(event, received, **fields)


def track_focus(stream, output="text"):
    # Lazy imports keep deterministic tests and CLI errors independent of macOS.
    import AppKit
    import Quartz
    from Foundation import NSBundle, NSDate, NSOperationQueue, NSRunLoop

    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("monitor must run on the main thread")
    workspace = AppKit.NSWorkspace.sharedWorkspace()
    center = workspace.notificationCenter()
    log = EventLog(stream, output)
    tracker = FocusTracker(log, lambda: input_ages(Quartz))
    observers = []
    stopped = False
    callback_errors = []

    def stop(signum, frame):
        nonlocal stopped
        stopped = True

    def callback(event):
        def receive(notification):
            nonlocal stopped
            received = time.monotonic()
            try:
                info = notification.userInfo() or {}
                app = app_metadata(info.get(AppKit.NSWorkspaceApplicationKey), NSBundle)
                tracker.observe(event, app, received)
            except Exception as exc:
                callback_errors.append(exc)
                stopped = True

        return receive

    old_handler = signal.signal(signal.SIGINT, stop)
    try:
        log.emit(
            "session_start",
            time.monotonic(),
            os_version=platform.mac_ver()[0],
            os_build=platform.version(),
            python_version=platform.python_version(),
            rapid_return_threshold_seconds=RAPID_RETURN_SECONDS,
            caveat="Receipt times and initial snapshot are not originating event "
            "times; input timing and rapid returns do not establish causality.",
        )
        notifications = (
            ("DidActivateApplication", "activation"),
            ("DidDeactivateApplication", "deactivation"),
            ("DidLaunchApplication", "launch"),
            ("DidTerminateApplication", "termination"),
            ("DidHideApplication", "hide"),
            ("DidUnhideApplication", "unhide"),
            ("ActiveSpaceDidChange", "space_change"),
        )
        for suffix, event in notifications:
            name = getattr(AppKit, "NSWorkspace" + suffix + "Notification")
            observers.append(
                center.addObserverForName_object_queue_usingBlock_(
                    name, None, NSOperationQueue.mainQueue(), callback(event)
                )
            )
        received = time.monotonic()
        tracker.observe(
            "initial_focus", app_metadata(workspace.frontmostApplication(), NSBundle), received
        )
        loop = NSRunLoop.mainRunLoop()
        while not stopped:
            loop.runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.2))
        if callback_errors:
            raise callback_errors[0]
        log.emit("session_stop", time.monotonic(), reason="SIGINT")
    finally:
        for observer in observers:
            center.removeObserver_(observer)
        signal.signal(signal.SIGINT, old_handler)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--log",
        type=Path,
        default=DEFAULT_LOG,
        help=f"append JSONL to this file (default: {DEFAULT_LOG})",
    )
    parser.add_argument(
        "--output",
        choices=("text", "json", "jsonl"),
        default="text",
        help="stdout format (default: text); json is sequential pretty "
        "event objects, jsonl is one compact record per line",
    )
    args = parser.parse_args(argv)
    try:
        with open_log(args.log) as stream:
            track_focus(stream, output=args.output)
    except (OSError, ImportError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
