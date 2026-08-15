#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Headless tests for the Recorder engine — no window, no Gtk.main().

This exists because the engine was extracted out of RecorderWindow precisely
so it could be driven without a GUI.  It exercises the state machine, the
callbacks, the auto-stop timer, the error path and mid-recording shutdown.

    python3 ref_docs/verify_engine.py

Exit code 0 = all tests passed.  Each test plays nothing and records the
system loopback for a couple of seconds; the audio content is not checked
here (verify_recorder.py covers that) — this is about control flow.
"""

import importlib.util
import os
import shutil
import sys
import tempfile

import gi

gi.require_version("Gst", "1.0")

from gi.repository import GLib, Gst  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "system_audio_recorder.py")

RESULTS = []


def load_app():
    spec = importlib.util.spec_from_file_location("recorder_app", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition), detail))
    print("  {0} {1}{2}".format(
        "PASS" if condition else "FAIL", name, (" — " + detail) if detail else ""))


def run_loop_until(predicate, timeout_seconds):
    """Turn a GLib main loop until predicate() is true or the timeout expires."""
    loop = GLib.MainLoop()
    state = {"timed_out": False}

    def poll():
        if predicate():
            loop.quit()
            return False
        return True

    def bail():
        state["timed_out"] = True
        loop.quit()
        return False

    GLib.timeout_add(100, poll)
    GLib.timeout_add_seconds(timeout_seconds, bail)
    loop.run()
    return not state["timed_out"]


def test_auto_stop_cycle(app, workdir):
    """start() -> ticks -> auto-stop -> finished, with states in order."""
    print("\n[1] full cycle with auto-stop")
    path = os.path.join(workdir, "cycle.wav")
    seen = {"states": [], "ticks": 0, "finished": None, "error": None}

    recorder = app.Recorder(
        on_state=lambda s: seen["states"].append(s),
        on_tick=lambda e: seen.update(ticks=e),
        on_error=lambda t, m: seen.update(error=(t, m)),
        on_finished=lambda p, size, forced: seen.update(finished=(p, size, forced)),
    )

    recorder.start("wav", app.MONITOR, path, limit_seconds=2)
    check("state is recording right after start", recorder.state == app.Recorder.RECORDING)
    check("engine owns the path", recorder.path == path)

    completed = run_loop_until(lambda: seen["finished"] is not None, 15)
    check("finished callback fired", completed and seen["finished"] is not None)
    check("no error reported", seen["error"] is None, str(seen["error"] or ""))
    check("returned to idle", recorder.state == app.Recorder.IDLE)
    check("ticked at least once", seen["ticks"] >= 1, "ticks={0}".format(seen["ticks"]))
    check("state order was recording->stopping->idle",
          seen["states"] == ["recording", "stopping", "idle"], str(seen["states"]))

    if seen["finished"]:
        finished_path, size, forced = seen["finished"]
        check("finished reported the right path", finished_path == path)
        check("file exists and is non-empty", os.path.exists(path) and size > 0,
              "{0} bytes".format(size))
        check("finish was clean, not forced", forced is False)
    check("elapsed reset to zero", recorder.elapsed == 0)


def test_manual_stop(app, workdir):
    """stop() mid-recording finalises the file."""
    print("\n[2] manual stop")
    path = os.path.join(workdir, "manual.opus")
    seen = {"finished": None}
    recorder = app.Recorder(
        on_finished=lambda p, size, forced: seen.update(finished=(p, size, forced))
    )
    recorder.start("opus", app.MONITOR, path)
    ran = run_loop_until(lambda: recorder.elapsed >= 2, 10)
    check("recorded for ~2s before stopping", ran, "elapsed={0}".format(recorder.elapsed))
    recorder.stop()
    check("state is stopping", recorder.state == app.Recorder.STOPPING)
    done = run_loop_until(lambda: seen["finished"] is not None, 15)
    check("finished after manual stop", done and seen["finished"] is not None)
    check("opus file is non-empty",
          os.path.exists(path) and os.path.getsize(path) > 0)


def test_start_errors(app, workdir):
    """Bad destinations raise RecorderError and leave the engine idle."""
    print("\n[3] error handling")
    recorder = app.Recorder()

    missing = os.path.join(workdir, "no-such-folder", "x.wav")
    try:
        recorder.start("wav", app.MONITOR, missing)
        check("missing folder raises", False, "no exception raised")
    except app.RecorderError as error:
        check("missing folder raises RecorderError", True)
        check("error carries a title", bool(error.title), error.title)
    check("engine still idle after failed start", recorder.state == app.Recorder.IDLE)

    # os.chmod cannot make a *directory* unwritable on Windows -- it only
    # toggles the read-only flag on files -- so os.access(..., W_OK) stays
    # True there and this check would report a failure that is not real.
    if sys.platform == "win32":
        print("  SKIP unwritable-folder check (chmod is a no-op on Windows)")
    else:
        readonly = os.path.join(workdir, "readonly")
        os.mkdir(readonly)
        os.chmod(readonly, 0o500)
        try:
            recorder.start("wav", app.MONITOR, os.path.join(readonly, "x.wav"))
            check("unwritable folder raises", False, "no exception raised")
        except app.RecorderError:
            check("unwritable folder raises RecorderError", True)
        finally:
            os.chmod(readonly, 0o700)
        check("engine idle after second failed start",
              recorder.state == app.Recorder.IDLE)


def test_shutdown_midway(app, workdir):
    """shutdown() during a recording still leaves a playable file."""
    print("\n[4] shutdown mid-recording (window-close path)")
    path = os.path.join(workdir, "closed.wav")
    recorder = app.Recorder()
    recorder.start("wav", app.MONITOR, path)
    run_loop_until(lambda: recorder.elapsed >= 2, 10)
    recorder.shutdown()
    check("idle after shutdown", recorder.state == app.Recorder.IDLE)
    check("file written and non-empty",
          os.path.exists(path) and os.path.getsize(path) > 1000,
          "{0} bytes".format(os.path.getsize(path) if os.path.exists(path) else 0))
    # A truncated WAV still opens; a finalised one reports a sane duration.
    import wave
    with wave.open(path, "rb") as handle:
        duration = handle.getnframes() / float(handle.getframerate() or 1)
    check("header was finalised with a real duration", duration >= 1.0,
          "{0:.2f}s".format(duration))


def test_double_start_is_ignored(app, workdir):
    """A second start() while active must not clobber the first recording."""
    print("\n[5] re-entrancy guard")
    path = os.path.join(workdir, "first.wav")
    other = os.path.join(workdir, "second.wav")
    recorder = app.Recorder()
    recorder.start("wav", app.MONITOR, path)
    recorder.start("wav", app.MONITOR, other)          # must be a no-op
    check("still recording the first file", recorder.path == path)
    check("second file was never created", not os.path.exists(other))
    recorder.shutdown()


def main():
    Gst.init(None)
    app = load_app()
    print("engine tests — device: {0}".format(app.MONITOR or "(system default)"))

    workdir = tempfile.mkdtemp(prefix="sysrec-engine-")
    try:
        test_auto_stop_cycle(app, workdir)
        test_manual_stop(app, workdir)
        test_start_errors(app, workdir)
        test_shutdown_midway(app, workdir)
        test_double_start_is_ignored(app, workdir)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    failed = [name for name, ok, _ in RESULTS if not ok]
    print("\n{0}/{1} checks passed".format(len(RESULTS) - len(failed), len(RESULTS)))
    if failed:
        print("RESULT: FAIL — " + "; ".join(failed))
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
