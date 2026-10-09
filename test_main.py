import contextlib
import io
import json
import re
from pathlib import Path
import signal
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import main


def app(pid):
    return dict(pid=pid, name=f"App {pid}", bundle_id=f"test.{pid}",
                version="1", build="2")


class FocusTests(unittest.TestCase):
    def setUp(self):
        self.stream = io.StringIO()
        self.stdout = io.StringIO()
        self.output = contextlib.redirect_stdout(self.stdout)
        self.output.__enter__()
        self.addCleanup(self.output.__exit__, None, None, None)
        self.tracker = main.FocusTracker(main.EventLog(self.stream), lambda: {})

    def records(self):
        return [json.loads(line) for line in self.stream.getvalue().splitlines()]

    def test_transition_deactivation_and_rapid_return(self):
        for event, pid, now in [("initial_focus", 1, 10), ("deactivation", 1, 12),
                                ("activation", 2, 12.2), ("activation", 1, 13)]:
            self.tracker.observe(event, app(pid), now)
        records = self.records()
        self.assertEqual(records[2]["previous_focus_seconds"], 2)
        self.assertAlmostEqual(records[3]["previous_focus_seconds"], .8)
        self.assertTrue(records[3]["rapid_return"])
        self.assertEqual(records[3]["previous_app"], app(2))

    def test_duplicate_null_and_unrelated_events_preserve_timing(self):
        for event, value, now in [("initial_focus", app(1), 10),
                                  ("activation", app(1), 11),
                                  ("activation", None, 12),
                                  ("deactivation", app(2), 13),
                                  ("space_change", None, 14),
                                  ("activation", app(2), 15),
                                  ("activation", app(1), 18)]:
            self.tracker.observe(event, value, now)
        records = self.records()
        self.assertTrue(records[1]["duplicate"])
        self.assertEqual(records[-2]["previous_focus_seconds"], 5)
        self.assertFalse(records[-1]["rapid_return"])

    def test_initial_unknown_and_duplicate_deactivation(self):
        self.tracker.observe("initial_focus", None, 1)
        self.tracker.observe("activation", app(1), 2)
        self.tracker.observe("deactivation", app(1), 3)
        self.tracker.observe("deactivation", app(1), 4)
        self.tracker.observe("activation", app(2), 5)
        self.assertEqual(self.records()[-1]["previous_focus_seconds"], 1)

    def test_same_app_reactivation_after_deactivation_restarts_interval(self):
        self.tracker.observe("initial_focus", app(1), 1)
        self.tracker.observe("deactivation", app(1), 2)
        self.tracker.observe("activation", app(1), 3)
        self.tracker.observe("activation", app(2), 4)
        records = self.records()
        self.assertFalse(records[2]["duplicate"])
        self.assertFalse(records[2]["rapid_return"])
        self.assertEqual(records[-1]["previous_focus_seconds"], 1)

    def test_jsonl_schema_and_privacy(self):
        self.tracker.observe("activation", app(1), 10)
        record = self.records()[0]
        self.assertEqual(record["schema_version"], 1)
        self.assertIn("+00:00", record["observed_at"])
        self.assertEqual(set(record["app"]), {"pid", "name", "bundle_id", "version", "build"})
        self.assertEqual(set(record), {"schema_version", "event", "observed_at",
                         "monotonic_seconds", "timing_basis", "app", "recent_input",
                         "duplicate", "previous_app", "previous_focus_seconds", "rapid_return"})
        self.assertIn("activation", self.stdout.getvalue())


class OutputTests(unittest.TestCase):
    def emit(self, mode, tty=False, environment=None):
        logfile = io.StringIO()
        stdout = io.StringIO()
        stdout.isatty = lambda: tty
        fields = dict(app={"name": 'Quote " slash \\ newline\nESC\033[31m café'},
                      missing=None, integer=-7, number=1.25e-20, flag=True,
                      nested=[False, {}, []])
        with patch.dict("os.environ", environment or {}, clear=True):
            with contextlib.redirect_stdout(stdout):
                log = main.EventLog(logfile, mode)
                log.emit("activation", 12.5, **fields)
                log.emit("session_stop", 13, reason="SIGINT")
        return logfile.getvalue(), stdout.getvalue()

    def decode_objects(self, text):
        decoder = json.JSONDecoder()
        records = []
        while text.strip():
            record, end = decoder.raw_decode(text.lstrip())
            records.append(record)
            text = text.lstrip()[end:]
        return records

    def test_plain_json_indentation_record_parity_and_safe_escaping(self):
        logfile, stdout = self.emit("json")
        self.assertEqual(self.decode_objects(stdout), self.decode_objects(logfile))
        self.assertIn('\n  "schema_version": 1,\n', stdout)
        self.assertNotIn("\033", stdout)
        self.assertIn(r'\u001b[31m', stdout)
        self.assertTrue(stdout.endswith("}\n"))
        self.assertEqual(len(logfile.splitlines()), 2)

    def test_colored_json_tty_record_parity(self):
        logfile, stdout = self.emit("json", tty=True)
        self.assertIn("\033[", stdout)
        plain = re.sub(r"\x1b\[[0-9;]*m", "", stdout)
        self.assertEqual(self.decode_objects(plain), self.decode_objects(logfile))
        self.assertNotIn("\033", logfile)
        self.assertIn(r'\u001b[31m', plain)

    def test_jsonl_always_compact_plain_and_complete(self):
        for tty in (False, True):
            with self.subTest(tty=tty):
                logfile, stdout = self.emit("jsonl", tty=tty)
                records = [json.loads(line) for line in stdout.splitlines()]
                self.assertEqual(records, self.decode_objects(logfile))
                self.assertNotIn("\033", stdout)
                self.assertEqual(stdout, "".join(json.dumps(record, ensure_ascii=True,
                                 allow_nan=False, separators=(",", ":")) + "\n"
                                 for record in records))

    def test_color_opt_out_on_tty(self):
        for environment in ({"NO_COLOR": ""}, {"NO_COLOR": "1"}, {"TERM": "dumb"}):
            with self.subTest(environment=environment):
                logfile, stdout = self.emit("json", tty=True, environment=environment)
                self.assertNotIn("\033", stdout)
                self.assertEqual(self.decode_objects(stdout), self.decode_objects(logfile))

    def test_text_default_unchanged(self):
        logfile = io.StringIO()
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            main.EventLog(logfile).emit("test", 1, value=None)
        record = json.loads(logfile.getvalue())
        self.assertEqual(stdout.getvalue(),
                         f"[{record['observed_at']}] test: {{\"value\": null}}\n")

    def test_flushes_log_and_stdout_in_every_mode(self):
        for mode in ("text", "json", "jsonl"):
            with self.subTest(mode=mode):
                logfile = io.StringIO()
                stdout = io.StringIO()
                with patch.object(logfile, "flush") as log_flush:
                    with patch.object(stdout, "flush") as stdout_flush:
                        with contextlib.redirect_stdout(stdout):
                            main.EventLog(logfile, mode).emit("test", 1)
                        log_flush.assert_called_once_with()
                        stdout_flush.assert_called_once_with()

    def test_cli_modes_wired_to_monitor(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "log"
            for mode in ("text", "json", "jsonl"):
                args = ["--log", str(path), "--output", mode]
                with patch.object(main, "track_focus") as monitor:
                    self.assertEqual(main.main(args), 0)
                    self.assertEqual(monitor.call_args.kwargs, {"output": mode})
            with patch.object(main, "track_focus") as monitor:
                self.assertEqual(main.main(["--log", str(path)]), 0)
                self.assertEqual(monitor.call_args.kwargs, {"output": "text"})

    def test_cli_invalid_output_and_help_do_not_start_monitor(self):
        with patch.object(main, "open_log") as open_log:
            with contextlib.redirect_stderr(io.StringIO()) as stderr:
                with self.assertRaises(SystemExit) as error:
                    main.main(["--output", "yaml"])
            self.assertEqual(error.exception.code, 2)
            self.assertIn("invalid choice", stderr.getvalue())
            with contextlib.redirect_stdout(io.StringIO()) as stdout:
                with self.assertRaises(SystemExit) as help_exit:
                    main.main(["--help"])
            self.assertEqual(help_exit.exception.code, 0)
            self.assertIn("--output {text,json,jsonl}", stdout.getvalue())
            open_log.assert_not_called()


class BoundaryTests(unittest.TestCase):
    def quartz(self, value):
        names = ["KeyDown", "KeyUp", "FlagsChanged", "LeftMouseDown", "LeftMouseUp",
                 "RightMouseDown", "RightMouseUp", "OtherMouseDown", "OtherMouseUp"]
        q = SimpleNamespace(kCGEventSourceStateCombinedSessionState=0)
        for index, name in enumerate(names):
            setattr(q, "kCGEvent" + name, index)
        q.CGEventSourceSecondsSinceLastEventType = lambda state, event: value
        return q

    def test_input_uncertainty_and_invalid_values(self):
        for value in (0, 5):
            result = main.input_ages(self.quartz(value))
            self.assertEqual(result["keyboard"]["status"], "reported_unverified")
            self.assertEqual(result["mouse_button"]["age_seconds"], value)
        for value in (-1, float("nan"), float("inf")):
            result = main.input_ages(self.quartz(value))
            self.assertEqual(result["keyboard"]["status"], "unavailable")
            self.assertIsNone(result["mouse_button"]["age_seconds"])
        q = self.quartz(1)
        def fail(*args):
            raise RuntimeError("private diagnostic not to be logged")
        q.CGEventSourceSecondsSinceLastEventType = fail
        self.assertEqual(main.input_ages(q)["keyboard"]["error_type"], "RuntimeError")
        self.assertEqual(main.input_ages(None)["keyboard"]["status"], "unavailable")

    def test_app_metadata_missing_bundle(self):
        native = SimpleNamespace(bundleURL=lambda: None, processIdentifier=lambda: 7,
                                 localizedName=lambda: None, bundleIdentifier=lambda: None)
        self.assertIsNone(main.app_metadata(None, None))
        self.assertEqual(main.app_metadata(native, None), dict(pid=7, name=None,
                         bundle_id=None, version=None, build=None))

    def test_safe_file_append_flush_and_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "log"
            with main.open_log(path) as stream, contextlib.redirect_stdout(io.StringIO()):
                main.EventLog(stream).emit("test", 1)
                self.assertEqual(json.loads(path.read_text())["event"], "test")
            with main.open_log(path) as stream:
                stream.write("second\n")
            self.assertTrue(path.read_text().endswith("second\n"))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            link = Path(directory) / "link"
            link.symlink_to(path)
            for bad in (link, Path(directory), Path(directory) / "absent" / "log"):
                with self.assertRaises(OSError):
                    main.open_log(bad)

    def test_cli_file_failure_and_stream_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main.main(["--log", directory]), 1)
            with patch.object(main, "track_focus") as monitor:
                self.assertEqual(main.main(["--log", str(Path(directory) / "log")]), 0)
                self.assertTrue(monitor.call_args.args[0].closed)
            with patch.object(main, "track_focus", side_effect=RuntimeError("failed")) as monitor:
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(main.main(["--log", str(Path(directory) / "log")]), 1)
                self.assertTrue(monitor.call_args.args[0].closed)

    def test_native_adapter_notifications_and_cleanup_with_local_fakes(self):
        callbacks = {}
        removed = []
        def register_callback(name, obj, queue, block):
            callbacks[name] = block
            return name
        center = SimpleNamespace(
            addObserverForName_object_queue_usingBlock_=register_callback,
            removeObserver_=removed.append)
        workspace = SimpleNamespace(notificationCenter=lambda: center,
                                    frontmostApplication=lambda: None)
        kit = SimpleNamespace(NSWorkspace=SimpleNamespace(sharedWorkspace=lambda: workspace),
                              NSWorkspaceApplicationKey="application")
        suffixes = ("DidActivateApplication", "DidDeactivateApplication", "DidLaunchApplication",
                    "DidTerminateApplication", "DidHideApplication", "DidUnhideApplication",
                    "ActiveSpaceDidChange")
        for suffix in suffixes:
            setattr(kit, "NSWorkspace" + suffix + "Notification", suffix)
        fail_callback = False
        def user_info():
            if fail_callback:
                raise RuntimeError("notification conversion failed")
            return None
        def run(date):
            for callback in callbacks.values():
                callback(SimpleNamespace(userInfo=user_info))
            signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
        foundation = SimpleNamespace(NSBundle=None,
            NSDate=SimpleNamespace(dateWithTimeIntervalSinceNow_=lambda value: value),
            NSOperationQueue=SimpleNamespace(mainQueue=lambda: None),
            NSRunLoop=SimpleNamespace(mainRunLoop=lambda: SimpleNamespace(runUntilDate_=run)))
        old = signal.getsignal(signal.SIGINT)
        stream = io.StringIO()
        stdout = io.StringIO()
        with patch.dict("sys.modules", AppKit=kit, Quartz=self.quartz(3), Foundation=foundation):
            with contextlib.redirect_stdout(stdout):
                main.track_focus(stream, output="jsonl")
        self.assertEqual(stdout.getvalue(), "".join(
            json.dumps(json.loads(line), separators=(",", ":")) + "\n"
            for line in stream.getvalue().splitlines()))
        events = [json.loads(line)["event"] for line in stream.getvalue().splitlines()]
        self.assertEqual(events, ["session_start", "initial_focus", "activation", "deactivation",
                                 "launch", "termination", "hide", "unhide", "space_change",
                                 "session_stop"])
        self.assertEqual(len(removed), 7)
        self.assertIs(signal.getsignal(signal.SIGINT), old)
        fail_callback = True
        removed.clear()
        with patch.dict("sys.modules", AppKit=kit, Quartz=self.quartz(3), Foundation=foundation):
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(RuntimeError, "notification conversion failed"):
                    main.track_focus(io.StringIO())
        self.assertEqual(len(removed), 7)
        self.assertIs(signal.getsignal(signal.SIGINT), old)

        removed.clear()
        count = 0
        def register(*args):
            nonlocal count
            count += 1
            if count == 2:
                raise RuntimeError("registration failed")
            return "first observer"
        center.addObserverForName_object_queue_usingBlock_ = register
        with patch.dict("sys.modules", AppKit=kit, Quartz=self.quartz(3), Foundation=foundation):
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(RuntimeError, "registration failed"):
                    main.track_focus(io.StringIO())
        self.assertEqual(removed, ["first observer"])
        self.assertIs(signal.getsignal(signal.SIGINT), old)


if __name__ == "__main__":
    unittest.main()
