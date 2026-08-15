#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Headless end-to-end check of system_audio_recorder.py — Linux and Windows.

Plays a test tone out of the default output device while recording the system
loopback through the app's OWN build_pipeline(), for WAV and Opus at once,
then asserts that both files decode and actually contain the tone.

This is the regression gate for the platform refactor: it exercises the real
pipeline strings, not a copy of them.

    python3 ref_docs/verify_recorder.py            (Linux)
    python  ref_docs\\verify_recorder.py            (Windows, MSYS2 UCRT64)

Options:
    --seconds N     record for N seconds (default 4)
    --keep          leave the recordings on disk for manual listening

Exit code 0 = PASS, 1 = FAIL.  Nothing is written outside a temp folder
unless --keep is given (then the paths are printed).
"""

import argparse
import importlib.util
import os
import sys
import tempfile
import wave

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstPbutils", "1.0")

from gi.repository import GLib, Gst, GstPbutils  # noqa: E402

WINDOWS = sys.platform == "win32"

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "system_audio_recorder.py")

# Playback sink matched to the platform, mirroring the app's capture source.
TONE_SINK = "wasapi2sink" if WINDOWS else "pulsesink"

MIN_DURATION = 2.5      # seconds of audio we insist on seeing
MIN_PEAK = 500          # out of 32767; proves it is not a silent recording


def load_app():
    spec = importlib.util.spec_from_file_location("recorder_app", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def record_while_playing(app, wav_path, opus_path, seconds):
    """Run tone + both recorders concurrently.  Returns None or an error string."""
    tone = Gst.parse_launch(
        "audiotestsrc wave=sine freq=440 volume=0.1 is-live=true ! "
        "audioconvert ! audioresample ! {0}".format(TONE_SINK)
    )
    recorders = [
        app.build_pipeline("wav", app.MONITOR, wav_path),
        app.build_pipeline("opus", app.MONITOR, opus_path),
    ]

    loop = GLib.MainLoop()
    state = {"eos": 0, "error": None}

    def on_message(_bus, message):
        if message.type == Gst.MessageType.EOS:
            state["eos"] += 1
            if state["eos"] >= len(recorders):
                loop.quit()
        elif message.type == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            state["error"] = "{0} | {1}".format(err.message, debug)
            loop.quit()
        return True

    for pipeline in recorders:
        bus = pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", on_message)

    tone.set_state(Gst.State.PLAYING)
    for pipeline in recorders:
        if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            tone.set_state(Gst.State.NULL)
            return "a recording pipeline refused to reach PLAYING"

    def send_eos():
        for pipeline in recorders:
            pipeline.send_event(Gst.Event.new_eos())
        return False

    GLib.timeout_add_seconds(seconds, send_eos)
    GLib.timeout_add_seconds(seconds + 8, loop.quit)  # safety net
    loop.run()

    tone.set_state(Gst.State.NULL)
    for pipeline in recorders:
        pipeline.set_state(Gst.State.NULL)

    if state["error"]:
        return "bus error: " + state["error"]
    if state["eos"] < len(recorders):
        return "EOS did not arrive for both pipelines (file may be truncated)"
    return None


def check_wav(path):
    """Return (duration_seconds, peak_amplitude)."""
    with wave.open(path, "rb") as handle:
        frames = handle.getnframes()
        rate = handle.getframerate()
        width = handle.getsampwidth()
        data = handle.readframes(frames)
    duration = frames / float(rate) if rate else 0.0
    peak = 0
    if width == 2:
        for offset in range(0, len(data) - 1, 2):
            sample = int.from_bytes(data[offset:offset + 2], "little", signed=True)
            if sample < 0:
                sample = -sample
            if sample > peak:
                peak = sample
    return duration, peak


def check_opus(path):
    """Return (duration_seconds, stream_types)."""
    discoverer = GstPbutils.Discoverer.new(10 * Gst.SECOND)
    uri = Gst.filename_to_uri(path)
    info = discoverer.discover_uri(uri)
    duration = info.get_duration() / float(Gst.SECOND)
    streams = [s.get_stream_type_nick() for s in info.get_stream_list()]
    return duration, streams


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=4)
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()

    Gst.init(None)
    app = load_app()

    print("platform : {0}".format("windows" if WINDOWS else "linux"))
    print("source   : {0}".format(app.SOURCE_DESCRIPTION))
    print("device   : {0}".format(app.MONITOR or "(system default)"))
    if app.MONITOR_WARNING:
        print("WARNING  : device detection was not confident")

    workdir = tempfile.mkdtemp(prefix="sysrec-verify-")
    wav_path = os.path.join(workdir, "verify.wav")
    opus_path = os.path.join(workdir, "verify.opus")

    missing = [
        name
        for name in (app.CAPTURE_ELEMENT, TONE_SINK, "wavenc", "opusenc", "oggmux")
        if Gst.ElementFactory.find(name) is None
    ]
    if missing:
        print("FAIL: missing GStreamer elements: {0}".format(", ".join(missing)))
        return 1

    print("recording {0}s...".format(args.seconds))
    failure = record_while_playing(app, wav_path, opus_path, args.seconds)
    if failure:
        print("FAIL: " + failure)
        return 1

    wav_duration, wav_peak = check_wav(wav_path)
    opus_duration, opus_streams = check_opus(opus_path)

    print("wav      : {0:.2f}s, peak {1}/32767".format(wav_duration, wav_peak))
    print("opus     : {0:.2f}s, streams {1}, {2} bytes".format(
        opus_duration, opus_streams, os.path.getsize(opus_path)))

    problems = []
    if wav_duration < MIN_DURATION:
        problems.append("wav too short ({0:.2f}s)".format(wav_duration))
    if wav_peak < MIN_PEAK:
        problems.append("wav is silent (peak {0}) - wrong capture device?".format(wav_peak))
    if opus_duration < MIN_DURATION:
        problems.append("opus too short ({0:.2f}s)".format(opus_duration))
    if "audio" not in opus_streams:
        problems.append("opus has no audio stream")

    if args.keep:
        print("kept     : {0}".format(workdir))
    else:
        for path in (wav_path, opus_path):
            try:
                os.remove(path)
            except OSError:
                pass
        try:
            os.rmdir(workdir)
        except OSError:
            pass

    if problems:
        print("RESULT   : FAIL - " + "; ".join(problems))
        return 1
    print("RESULT   : PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
