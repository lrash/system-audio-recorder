#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# =============================================================================
#  System Audio Recorder
#  ---------------------------------------------------------------------------
#  Records what you HEAR (the system audio output loopback) on:
#    * Linux   - Ubuntu 20.04+ with PulseAudio or PipeWire (pipewire-pulse)
#    * Windows - Windows 10/11 via WASAPI loopback
#
#  Author:    lonestarparrot <lonestarparrot@gmail.com>
#  Source:    https://github.com/lrash/system-audio-recorder
#  Copyright (c) 2026 lonestarparrot.  All rights reserved.
#
#  Release integrity: signed builds carry a detached OpenPGP signature next
#  to the file (see SHA256SUMS.asc) made with key 53D4586CB4F47C1D.
#
#  Pure GStreamer + PyGObject.  No FFmpeg, no subprocess recording.
#
#  On Linux the capture device must be named explicitly, so `pactl` is run ONCE
#  at startup to discover the monitor source (falling back to `pw-dump` on
#  PipeWire boxes without pulseaudio-utils).  On Windows `wasapi2src` captures
#  the default render device on its own, so no discovery step is needed.
#
#  ---------------------------------------------------------------------------
#  HOW TO INSTALL DEPENDENCIES  (run this once, before first launch)
#  ---------------------------------------------------------------------------
#
#  LINUX (Debian/Ubuntu):
#
#    sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0 \
#          gstreamer1.0-plugins-good gstreamer1.0-libav \
#          gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly
#
#  Also recommended (usually already present as dependencies of the above):
#
#    sudo apt install gir1.2-gstreamer-1.0 gstreamer1.0-pulseaudio \
#          gstreamer1.0-plugins-base pulseaudio-utils
#
#  Then run:   python3 system_audio_recorder.py
#
#  WINDOWS (MSYS2 - https://www.msys2.org/), from the UCRT64 shell:
#
#    pacman -S --needed mingw-w64-ucrt-x86_64-python \
#          mingw-w64-ucrt-x86_64-python-gobject \
#          mingw-w64-ucrt-x86_64-gtk3 \
#          mingw-w64-ucrt-x86_64-gstreamer \
#          mingw-w64-ucrt-x86_64-gst-plugins-base \
#          mingw-w64-ucrt-x86_64-gst-plugins-good \
#          mingw-w64-ucrt-x86_64-gst-plugins-bad
#
#    Then run:   python system_audio_recorder.py
#    (or `pythonw system_audio_recorder.py` for no console window)
#
#  ---------------------------------------------------------------------------
#  Element origins (for troubleshooting "no element ..." errors):
#    pulsesrc, wavenc      -> gstreamer1.0-pulseaudio / -plugins-good
#    wasapi2src (Windows)  -> gst-plugins-bad (GStreamer >= 1.18)
#    audioconvert, resample-> gstreamer1.0-plugins-base
#    opusenc               -> gstreamer1.0-plugins-base (>=1.14) or -plugins-bad
#    oggmux                -> gstreamer1.0-plugins-base
# =============================================================================

import datetime
import json
import os
import subprocess
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gst", "1.0")
gi.require_version("Pango", "1.0")

from gi.repository import GLib, Gst, Gtk, Pango  # noqa: E402  (after require_version)


__author__ = "lonestarparrot"
__email__ = "lonestarparrot@gmail.com"
__copyright__ = "Copyright (c) 2026 lonestarparrot"
__version__ = "0.3.0"
__url__ = "https://github.com/lrash/system-audio-recorder"
__signing_key__ = "53CAF98A8019832F911E3BA653D4586CB4F47C1D"

APP_TITLE = "System Audio Recorder"
# Reverse-DNS id shared by the .desktop file, the icon and the Wayland app id.
APP_ID = "io.github.lrash.SystemAudioRecorder"

WINDOWS = sys.platform == "win32"

# Last-resort Linux device name if `pactl`/`pw-dump` report no monitor.
# Unused on Windows, where the source finds the default render device itself.
FALLBACK_MONITOR = "alsa_output.pci-0000_00_1f.3.analog-stereo.monitor"

if WINDOWS:
    # wasapi2src with loopback=true captures the default *render* endpoint --
    # i.e. what you hear -- with no device name required.
    #
    # `audiorate` is deliberate insurance: WASAPI loopback historically stops
    # delivering buffers while nothing is playing, which would shorten the
    # recording rather than record silence.  audiorate fills such gaps to keep
    # wall-clock and media time in step, and is an inert passthrough when the
    # source already behaves (which wasapi2src does on current Windows 10/11).
    SOURCE_DESCRIPTION = "wasapi2src name=src loopback=true ! audiorate"
    CAPTURE_ELEMENT = "wasapi2src"
    DEVICE_LIST_HINT = "gst-device-monitor-1.0 Audio/Sink"
    INSTALL_HINT = (
        "Install them from the MSYS2 UCRT64 shell with:\n"
        "    pacman -S --needed mingw-w64-ucrt-x86_64-gstreamer \\\n"
        "          mingw-w64-ucrt-x86_64-gst-plugins-base \\\n"
        "          mingw-w64-ucrt-x86_64-gst-plugins-good \\\n"
        "          mingw-w64-ucrt-x86_64-gst-plugins-bad"
    )
else:
    SOURCE_DESCRIPTION = "pulsesrc name=src"
    CAPTURE_ELEMENT = "pulsesrc"
    DEVICE_LIST_HINT = "pactl list short sources | grep monitor"
    INSTALL_HINT = (
        "Install them with:\n"
        "    sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0 \\\n"
        "          gstreamer1.0-plugins-good gstreamer1.0-libav \\\n"
        "          gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly"
    )

# Formats: key -> (label, file extension, encode/mux tail of the pipeline).
# The capture source is prepended per platform by pipeline_description().
FORMATS = {
    "wav": {
        "label": "WAV",
        "ext": ".wav",
        "mime_pattern": "*.wav",
        "encoder": (
            "audioconvert ! audioresample ! "
            "audio/x-raw,format=S16LE,rate=44100,channels=2 ! "
            "wavenc"
        ),
    },
    "opus": {
        "label": "Opus",
        "ext": ".opus",
        "mime_pattern": "*.opus",
        "encoder": (
            "audioconvert ! audioresample ! "
            "opusenc bitrate=128000 ! "
            "oggmux"
        ),
    },
}

KNOWN_EXTENSIONS = tuple(f["ext"] for f in FORMATS.values()) + (".ogg", ".oga")

# How long to wait (ms) for the EOS to travel the pipeline before forcing NULL.
EOS_TIMEOUT_MS = 4000


# -----------------------------------------------------------------------------
# Monitor (loopback) source detection
# -----------------------------------------------------------------------------
def _pactl(*args):
    """Run `pactl <args>` and return stdout, or None on any failure."""
    try:
        result = subprocess.run(
            ["pactl", *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            universal_newlines=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def list_monitor_sources():
    """Return every source name containing '.monitor' (equiv. of `... | grep monitor`)."""
    output = _pactl("list", "short", "sources")
    if not output:
        return []
    names = []
    for line in output.splitlines():
        fields = line.split("\t")
        if len(fields) < 2:
            fields = line.split()
        if len(fields) < 2:
            continue
        name = fields[1].strip()
        if ".monitor" in name and name not in names:
            names.append(name)
    return names


def default_sink_name():
    """Return the name of the default sink, or None."""
    output = _pactl("info")
    if not output:
        return None
    for line in output.splitlines():
        if line.startswith("Default Sink:"):
            return line.split(":", 1)[1].strip() or None
    return None


def _pw_dump():
    """Run `pw-dump` and return the parsed JSON, or None on any failure."""
    try:
        result = subprocess.run(
            ["pw-dump"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            universal_newlines=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except ValueError:
        return None


def _pw_sink_names(dump):
    """Return every Audio/Sink node name from a pw-dump listing."""
    names = []
    for obj in dump or []:
        props = (obj.get("info") or {}).get("props") or {}
        if props.get("media.class") != "Audio/Sink":
            continue
        name = props.get("node.name")
        if name and name not in names:
            names.append(name)
    return names


def _pw_default_sink_name(dump):
    """Return the default sink node name from pw-dump metadata, or None."""
    for obj in dump or []:
        if obj.get("type") != "PipeWire:Interface:Metadata":
            continue
        if (obj.get("props") or {}).get("metadata.name") != "default":
            continue
        for entry in obj.get("metadata") or []:
            if entry.get("key") == "default.audio.sink":
                value = entry.get("value")
                if isinstance(value, dict):
                    return value.get("name")
    return None


def detect_monitor():
    """
    Detect the loopback capture device for the default output.

    On Windows there is nothing to detect: wasapi2src with loopback=true
    already follows the default render endpoint, so ("", None) is returned and
    the source is left on its own default.

    On Linux the device must be named, so this tries `pactl` first (PulseAudio,
    or PipeWire via pipewire-pulse), then falls back to `pw-dump` on PipeWire
    boxes without pulseaudio-utils — pipewire-pulse exposes each sink's monitor
    as "<node.name>.monitor".

    Returns (device_name, warning_or_None).  The warning is a human-readable
    string when detection was not reliable and the user should double-check.
    """
    if WINDOWS:
        return "", None

    monitors = list_monitor_sources()
    sink = default_sink_name()

    if not monitors and not sink:
        dump = _pw_dump()
        monitors = [name + ".monitor" for name in _pw_sink_names(dump)]
        sink = _pw_default_sink_name(dump)

    if sink:
        candidate = sink if sink.endswith(".monitor") else sink + ".monitor"
        if candidate in monitors:
            return candidate, None
        if not monitors:
            # `pactl info` worked but the source listing did not: still a good guess.
            return candidate, None

    if monitors:
        return monitors[0], None

    return (
        FALLBACK_MONITOR,
        "No PulseAudio/PipeWire monitor source could be detected "
        "(is `pactl` or `pw-dump` installed and the sound server running?).\n\n"
        "Falling back to:\n    {0}\n\n"
        "If recording produces silence, open \"Advanced\" in the window and "
        "enter the correct device name. You can list the available ones with:\n"
        "    pactl list short sources | grep monitor".format(FALLBACK_MONITOR),
    )


# Detected once at startup, as required.
MONITOR, MONITOR_WARNING = detect_monitor()


# -----------------------------------------------------------------------------
# Pipeline construction
# -----------------------------------------------------------------------------
def pipeline_description(fmt_key):
    """Return the gst-launch style description for the given format key."""
    return "{0} ! {1} ! filesink name=sink".format(
        SOURCE_DESCRIPTION, FORMATS[fmt_key]["encoder"]
    )


def build_pipeline(fmt_key, device, location):
    """
    Build and return a ready-to-play Gst.Pipeline.

    The element chain is parsed from the description above; `device` and
    `location` are applied as properties afterwards so that names containing
    spaces, quotes or other shell-significant characters are handled safely.

    `device` may be None or empty, which leaves the source on its own default.
    That is the normal case on Windows (wasapi2src follows the default render
    endpoint) and the last-resort case on Linux.

    Raises GLib.Error if the description cannot be parsed (missing plugin, ...)
    or RuntimeError if the named elements are unexpectedly absent.
    """
    pipeline = Gst.parse_launch(pipeline_description(fmt_key))

    src = pipeline.get_by_name("src")
    sink = pipeline.get_by_name("sink")
    if src is None or sink is None:
        raise RuntimeError("Internal error: pipeline is missing 'src' or 'sink'.")

    if device:
        src.set_property("device", device)
    # Keep the ring buffer modest so stopping is snappy.  Both properties are
    # pulsesrc-specific; wasapi2src manages its own buffering.
    for prop, value in (("buffer-time", 200000), ("latency-time", 20000)):
        try:
            src.set_property(prop, value)
        except TypeError:
            pass  # Source without these properties (wasapi2src, older pulsesrc).

    sink.set_property("location", location)
    sink.set_property("sync", False)

    return pipeline


def home_directory():
    """
    The user's home folder.

    On Windows under MSYS2, os.path.expanduser("~") resolves to the MSYS2 home
    (C:\\msys64\\home\\<user>) rather than the Windows profile, which is not
    where anyone expects their recordings.  Prefer USERPROFILE there.
    """
    if WINDOWS:
        profile = os.environ.get("USERPROFILE")
        if profile and os.path.isdir(profile):
            return profile
    return os.path.expanduser("~")


def default_filename(ext):
    """<home>/sysrec-YYYYMMDD-HHMMSS.<ext>"""
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return os.path.join(home_directory(), "sysrec-{0}{1}".format(stamp, ext))


def swap_extension(path, new_ext):
    """Replace a known audio extension with `new_ext` (append if none present)."""
    root, ext = os.path.splitext(path)
    if ext.lower() in KNOWN_EXTENSIONS:
        return root + new_ext
    return path + new_ext


# -----------------------------------------------------------------------------
# Identity / about
# -----------------------------------------------------------------------------
def about_lines():
    """Author and build details, shared by --about and the About dialog."""
    return [
        ("Application", "{0} {1}".format(APP_TITLE, __version__)),
        ("Author", "{0} <{1}>".format(__author__, __email__)),
        ("Copyright", __copyright__),
        ("Source", __url__),
        ("App ID", APP_ID),
        ("Signing key", __signing_key__),
        ("Platform", "Windows (WASAPI loopback)" if WINDOWS else "Linux (PulseAudio / PipeWire)"),
        ("Capture", SOURCE_DESCRIPTION),
        ("Device", MONITOR or "(system default)"),
    ]


def about_text():
    """The same details as a plain block, for stdout."""
    width = max(len(label) for label, _ in about_lines())
    return "\n".join(
        "{0:<{1}} : {2}".format(label, width, value) for label, value in about_lines()
    )


def show_about_dialog(parent=None):
    """GTK About box -- the clickable equivalent of `--about`."""
    dialog = Gtk.AboutDialog(transient_for=parent, modal=parent is not None)
    dialog.set_program_name(APP_TITLE)
    dialog.set_version(__version__)
    dialog.set_comments(
        "Records system audio output -- what you hear -- to WAV or Opus."
    )
    dialog.set_copyright(__copyright__)
    dialog.set_authors(["{0} <{1}>".format(__author__, __email__)])
    dialog.set_website(__url__)
    dialog.set_website_label("Source on GitHub")
    dialog.set_logo_icon_name(APP_ID)
    dialog.set_license_type(Gtk.License.CUSTOM)
    dialog.set_license(
        "{0}\nAll rights reserved.\n\n"
        "Releases are signed with OpenPGP key\n{1}\n\n"
        "Verify a copy with:\n"
        "    gpg --verify SHA256SUMS.asc SHA256SUMS\n"
        "    sha256sum -c SHA256SUMS".format(__copyright__, __signing_key__)
    )
    dialog.run()
    dialog.destroy()


# -----------------------------------------------------------------------------
# Recording engine
# -----------------------------------------------------------------------------
class RecorderError(Exception):
    """A start-up failure carrying a title and a human-readable explanation."""

    def __init__(self, title, message):
        Exception.__init__(self, "{0}: {1}".format(title, message))
        self.title = title
        self.message = message


class Recorder(object):
    """
    The recording engine: pipeline lifecycle, state machine and timers.

    Deliberately free of any GUI dependency -- it uses GLib (timers, main-loop
    dispatch) but never Gtk, and it neither reads nor writes a widget.  A view
    drives it through start()/stop() and learns about progress through the
    callbacks handed to __init__, so the same engine can back a window, a test
    harness or a headless caller.

    State machine:  idle -> recording -> stopping -> idle

    Callbacks (all optional):
        on_state(state)                  state changed; see the constants below
        on_tick(elapsed_seconds)         once a second while recording
        on_error(title, message)         asynchronous failure; state is idle
        on_finished(path, size, forced)  file closed; `forced` means the EOS
                                         timed out and the pipeline was cut
    """

    IDLE = "idle"
    RECORDING = "recording"
    STOPPING = "stopping"

    def __init__(self, on_state=None, on_tick=None, on_error=None, on_finished=None):
        self._on_state = on_state or (lambda state: None)
        self._on_tick = on_tick or (lambda elapsed: None)
        self._on_error = on_error or (lambda title, message: None)
        self._on_finished = on_finished or (lambda path, size, forced: None)

        # Public, and the source of truth: no widget holds this state.
        self.state = self.IDLE
        self.path = None
        self.format_key = None
        self.device = None
        self.elapsed = 0

        self._pipeline = None
        self._bus = None
        self._tick_id = 0
        self._duration_id = 0
        self._eos_timeout_id = 0

    @property
    def is_active(self):
        """True while recording or finalising."""
        return self.state != self.IDLE

    # -- control ------------------------------------------------------------
    def start(self, fmt_key, device, path, limit_seconds=0):
        """
        Begin recording `fmt_key` from `device` into `path`.

        `limit_seconds` > 0 stops automatically after that long.  Raises
        RecorderError when recording cannot begin; the caller decides how to
        present it.
        """
        if self.is_active:
            return

        directory = os.path.dirname(path)
        if not os.path.isdir(directory):
            raise RecorderError(
                "Cannot start recording", "Folder does not exist:\n" + directory
            )
        if not os.access(directory, os.W_OK):
            raise RecorderError(
                "Cannot start recording", "Folder is not writable:\n" + directory
            )

        try:
            self._pipeline = build_pipeline(fmt_key, device, path)
        except GLib.Error as error:
            self._pipeline = None
            raise RecorderError(
                "Could not build the GStreamer pipeline",
                "{0}\n\nPipeline:\n{1}\n\n"
                "A required plugin is probably missing.\n\n{2}".format(
                    error.message, pipeline_description(fmt_key), INSTALL_HINT
                ),
            )
        except RuntimeError as error:
            self._pipeline = None
            raise RecorderError("Could not build the GStreamer pipeline", str(error))

        self._bus = self._pipeline.get_bus()
        self._bus.add_signal_watch()
        self._bus.connect("message::error", self._handle_bus_error)
        self._bus.connect("message::warning", self._handle_bus_warning)
        self._bus.connect("message::eos", self._handle_bus_eos)

        if self._pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            self._teardown()
            raise RecorderError(
                "Could not start recording",
                "GStreamer refused to start the pipeline.\n\n"
                + (
                    "Capture device: {0}\n\n".format(device)
                    if device
                    else "Capture device: (system default)\n\n"
                )
                + "List the available devices with:\n    " + DEVICE_LIST_HINT,
            )

        self.path = path
        self.format_key = fmt_key
        self.device = device
        self.elapsed = 0
        self._set_state(self.RECORDING)

        self._tick_id = GLib.timeout_add_seconds(1, self._tick)
        if limit_seconds > 0:
            self._duration_id = GLib.timeout_add_seconds(
                limit_seconds, self._duration_reached
            )

    def stop(self):
        """
        Ask for a clean stop.

        EOS is sent so wavenc/oggmux flush their headers and the file is
        valid; the state becomes "stopping" until that completes or times out.
        """
        if self.state != self.RECORDING:
            return
        self._cancel("_tick_id", "_duration_id")
        self._set_state(self.STOPPING)
        self._pipeline.send_event(Gst.Event.new_eos())
        self._eos_timeout_id = GLib.timeout_add(EOS_TIMEOUT_MS, self._eos_timed_out)

    def abort(self):
        """Drop the pipeline and return to idle without finalising the file."""
        self._cancel("_tick_id", "_duration_id", "_eos_timeout_id")
        self._teardown()
        self.elapsed = 0
        self._set_state(self.IDLE)

    def shutdown(self):
        """
        Best-effort synchronous finalisation, for application exit.

        Unlike stop() this does not wait on the main loop -- which is about to
        stop turning -- so it pumps the bus directly.  No callbacks are fired.
        """
        self._cancel("_tick_id", "_duration_id", "_eos_timeout_id")
        if self._pipeline is not None and self.is_active:
            # Flush the encoder/muxer so a file closed by window-close plays.
            self._pipeline.send_event(Gst.Event.new_eos())
            self._pipeline.get_bus().timed_pop_filtered(
                2 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR
            )
        self.state = self.IDLE  # set directly: no observer left to notify
        self._teardown()

    # -- internals ----------------------------------------------------------
    def _set_state(self, state):
        self.state = state
        self._on_state(state)

    def _cancel(self, *names):
        for name in names:
            source_id = getattr(self, name)
            if source_id:
                GLib.source_remove(source_id)
                setattr(self, name, 0)

    def _teardown(self):
        if self._bus is not None:
            try:
                self._bus.remove_signal_watch()
            except Exception:
                pass
            self._bus = None
        if self._pipeline is not None:
            self._pipeline.set_state(Gst.State.NULL)
            self._pipeline = None

    def _finish(self, forced):
        path = self.path
        self._cancel("_tick_id", "_duration_id", "_eos_timeout_id")
        self._teardown()
        self.elapsed = 0
        self._set_state(self.IDLE)
        size = os.path.getsize(path) if path and os.path.exists(path) else 0
        self._on_finished(path, size, forced)

    def _tick(self):
        if self.state != self.RECORDING:
            self._tick_id = 0
            return False
        self.elapsed += 1
        self._on_tick(self.elapsed)
        return True

    def _duration_reached(self):
        self._duration_id = 0
        self.stop()
        return False

    def _eos_timed_out(self):
        self._eos_timeout_id = 0
        if self.state == self.STOPPING:
            self._finish(forced=True)
        return False

    # The bus watch dispatches on the main loop; these handlers tear the watch
    # itself down, so the real work is deferred to an idle callback.
    def _handle_bus_eos(self, _bus, _message):
        self._cancel("_eos_timeout_id")
        if self.is_active:
            GLib.idle_add(self._deferred_finish)

    def _deferred_finish(self):
        if self.is_active:
            self._finish(forced=False)
        return False

    def _handle_bus_warning(self, _bus, message):
        warning, _debug = message.parse_warning()
        sys.stderr.write("[gst warning] {0}\n".format(warning.message))

    def _handle_bus_error(self, _bus, message):
        error, debug = message.parse_error()
        details = error.message
        if debug:
            details += "\n\n" + debug
        GLib.idle_add(self._deferred_error, details)

    def _deferred_error(self, details):
        if self._pipeline is not None:
            self.abort()
            self._on_error("Recording error", details)
        return False


# -----------------------------------------------------------------------------
# Main window
# -----------------------------------------------------------------------------
class RecorderWindow(Gtk.Window):
    def __init__(self):
        Gtk.Window.__init__(self, title=APP_TITLE)

        self.set_default_size(400, 140)
        self.set_border_width(10)
        self.set_position(Gtk.WindowPosition.CENTER)
        # Minimally resizable: width may grow, height stays compact.
        self.set_resizable(True)

        # --- state -----------------------------------------------------------
        # Recording state lives in the engine; only view concerns are kept here.
        self.auto_filename = True          # filename still the generated default?
        self.updating_toggle = False       # guard against signal recursion
        self.recorder = Recorder(
            on_state=self.on_recorder_state,
            on_tick=self.on_recorder_tick,
            on_error=self.on_recorder_error,
            on_finished=self.on_recorder_finished,
        )

        # --- layout ----------------------------------------------------------
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.add(outer)

        # Row 1: format selection
        row_format = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.radio_wav = Gtk.RadioButton.new_with_label_from_widget(
            None, FORMATS["wav"]["label"]
        )
        self.radio_opus = Gtk.RadioButton.new_with_label_from_widget(
            self.radio_wav, FORMATS["opus"]["label"]
        )
        self.radio_wav.set_active(True)  # WAV is the default
        self.radio_wav.connect("toggled", self.on_format_toggled, "wav")
        self.radio_opus.connect("toggled", self.on_format_toggled, "opus")
        row_format.pack_start(Gtk.Label(label="Format:"), False, False, 0)
        row_format.pack_start(self.radio_wav, False, False, 0)
        row_format.pack_start(self.radio_opus, False, False, 0)
        outer.pack_start(row_format, False, False, 0)

        # Row 2: output file + Browse...
        row_file = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.entry_file = Gtk.Entry()
        self.entry_file.set_text(default_filename(FORMATS["wav"]["ext"]))
        self.entry_file.set_hexpand(True)
        self.entry_file.connect("changed", self.on_filename_edited)
        self.button_browse = Gtk.Button(label="Browse...")
        self.button_browse.connect("clicked", self.on_browse_clicked)
        row_file.pack_start(self.entry_file, True, True, 0)
        row_file.pack_start(self.button_browse, False, False, 0)
        outer.pack_start(row_file, False, False, 0)

        # Row 3: Start / Stop toggle
        self.button_record = Gtk.ToggleButton(label="Start Recording")
        self.button_record.connect("toggled", self.on_record_toggled)
        outer.pack_start(self.button_record, False, False, 0)

        # Advanced (bonus): device override + optional auto-stop timeout
        self.expander = Gtk.Expander(label="Advanced")
        adv = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        adv.set_border_width(6)

        row_device = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        row_device.pack_start(Gtk.Label(label="Device:"), False, False, 0)
        self.entry_device = Gtk.Entry()
        self.entry_device.set_text(MONITOR)
        if WINDOWS:
            self.entry_device.set_placeholder_text("(default output device)")
            self.entry_device.set_tooltip_text(
                "Leave empty to follow the default output device.\n"
                "To capture a specific one, paste its WASAPI device id.\n"
                "List them with:  gst-device-monitor-1.0 Audio/Sink"
            )
        else:
            self.entry_device.set_tooltip_text(
                "PulseAudio/PipeWire monitor source.\n"
                "List them with:  pactl list short sources | grep monitor"
            )
        row_device.pack_start(self.entry_device, True, True, 0)
        adv.pack_start(row_device, False, False, 0)

        row_limit = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        row_limit.pack_start(Gtk.Label(label="Auto-stop after:"), False, False, 0)
        adjustment = Gtk.Adjustment(
            value=0, lower=0, upper=86400, step_increment=10, page_increment=60
        )
        self.spin_limit = Gtk.SpinButton()
        self.spin_limit.set_adjustment(adjustment)
        self.spin_limit.set_numeric(True)
        self.spin_limit.set_tooltip_text("0 = record until you press Stop.")
        row_limit.pack_start(self.spin_limit, False, False, 0)
        row_limit.pack_start(Gtk.Label(label="seconds (0 = unlimited)"), False, False, 0)
        adv.pack_start(row_limit, False, False, 0)

        self.expander.add(adv)
        outer.pack_start(self.expander, False, False, 0)

        # Bottom: status label, with About tucked on the right
        row_status = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.label_status = Gtk.Label(label="Idle")
        self.label_status.set_xalign(0.0)
        self.label_status.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.label_status.set_selectable(True)
        row_status.pack_start(self.label_status, True, True, 0)

        self.button_about = Gtk.Button(label="About")
        self.button_about.set_relief(Gtk.ReliefStyle.NONE)
        self.button_about.set_tooltip_text(
            "Version, author and copyright details"
        )
        self.button_about.connect("clicked", self.on_about_clicked)
        row_status.pack_start(self.button_about, False, False, 0)
        outer.pack_start(row_status, False, False, 0)

        self.connect("delete-event", self.on_delete_event)

    # -- helpers ------------------------------------------------------------
    @property
    def format_key(self):
        return "wav" if self.radio_wav.get_active() else "opus"

    def set_status(self, text):
        self.label_status.set_text(text)

    def show_error(self, title, message):
        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text=title,
        )
        dialog.format_secondary_text(message)
        dialog.run()
        dialog.destroy()

    def show_warning(self, title, message):
        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.OK,
            text=title,
        )
        dialog.format_secondary_text(message)
        dialog.run()
        dialog.destroy()

    def set_controls_sensitive(self, sensitive):
        for widget in (
            self.radio_wav,
            self.radio_opus,
            self.entry_file,
            self.button_browse,
            self.entry_device,
            self.spin_limit,
        ):
            widget.set_sensitive(sensitive)

    def set_toggle_state(self, active, label):
        self.updating_toggle = True
        self.button_record.set_active(active)
        self.button_record.set_label(label)
        self.updating_toggle = False

    # -- UI callbacks -------------------------------------------------------
    def on_filename_edited(self, _entry):
        # Any manual edit means the name is no longer auto-generated.
        if not self.updating_toggle:
            self.auto_filename = False

    def on_format_toggled(self, button, key):
        if not button.get_active() or self.recorder.is_active:
            return
        ext = FORMATS[key]["ext"]
        current = self.entry_file.get_text().strip()
        was_auto = self.auto_filename
        self.updating_toggle = True
        if not current or was_auto:
            self.entry_file.set_text(default_filename(ext))
        else:
            self.entry_file.set_text(swap_extension(current, ext))
        self.updating_toggle = False
        self.auto_filename = was_auto

    def on_browse_clicked(self, _button):
        fmt = FORMATS[self.format_key]
        dialog = Gtk.FileChooserDialog(
            title="Save recording as",
            transient_for=self,
            action=Gtk.FileChooserAction.SAVE,
        )
        dialog.add_buttons(
            "_Cancel", Gtk.ResponseType.CANCEL,
            "_Save", Gtk.ResponseType.ACCEPT,
        )
        dialog.set_do_overwrite_confirmation(True)

        # Filter by the currently selected format.
        audio_filter = Gtk.FileFilter()
        audio_filter.set_name("{0} files ({1})".format(fmt["label"], fmt["mime_pattern"]))
        audio_filter.add_pattern(fmt["mime_pattern"])
        dialog.add_filter(audio_filter)

        all_filter = Gtk.FileFilter()
        all_filter.set_name("All files (*)")
        all_filter.add_pattern("*")
        dialog.add_filter(all_filter)

        current = self.entry_file.get_text().strip() or default_filename(fmt["ext"])
        folder = os.path.dirname(current) or home_directory()
        if os.path.isdir(folder):
            dialog.set_current_folder(folder)
        dialog.set_current_name(os.path.basename(current))

        if dialog.run() == Gtk.ResponseType.ACCEPT:
            chosen = dialog.get_filename()
            # Always make the extension match the selected format.
            if os.path.splitext(chosen)[1].lower() != fmt["ext"]:
                chosen = swap_extension(chosen, fmt["ext"])
            self.entry_file.set_text(chosen)
            self.auto_filename = False
        dialog.destroy()

    def on_record_toggled(self, button):
        if self.updating_toggle:
            return
        if button.get_active():
            self.start_recording()
        else:
            self.stop_recording()

    def on_about_clicked(self, _button):
        show_about_dialog(self)

    def on_delete_event(self, *_args):
        self.shutdown()
        Gtk.main_quit()
        return False

    # -- recording (thin: the engine owns the work) --------------------------
    def start_recording(self):
        if self.recorder.is_active:
            return

        path = self.entry_file.get_text().strip()
        fmt = FORMATS[self.format_key]

        if not path:
            path = default_filename(fmt["ext"])
        if os.path.splitext(path)[1].lower() != fmt["ext"]:
            path = swap_extension(path, fmt["ext"])
        # A fresh timestamp for every take, as long as the user never renamed it.
        if self.auto_filename and os.path.exists(path):
            path = default_filename(fmt["ext"])
        path = os.path.abspath(os.path.expanduser(path))
        self.updating_toggle = True
        self.entry_file.set_text(path)
        self.updating_toggle = False

        device = self.entry_device.get_text().strip() or MONITOR

        try:
            self.recorder.start(
                self.format_key, device, path, int(self.spin_limit.get_value())
            )
        except RecorderError as error:
            self.set_toggle_state(False, "Start Recording")
            self.set_status("Idle - " + error.title.lower())
            self.show_error(error.title, error.message)

    def stop_recording(self):
        self.recorder.stop()

    # -- engine callbacks ---------------------------------------------------
    def on_recorder_state(self, state):
        if state == Recorder.RECORDING:
            self.set_controls_sensitive(False)
            self.set_toggle_state(True, "Stop Recording")
            self.update_recording_status()
        elif state == Recorder.STOPPING:
            self.button_record.set_sensitive(False)
            self.set_status("Finalising file...")
        else:  # Recorder.IDLE
            self.button_record.set_sensitive(True)
            self.set_controls_sensitive(True)
            self.set_toggle_state(False, "Start Recording")
            # Next take gets a fresh timestamp if the user never picked a name.
            if self.auto_filename:
                self.updating_toggle = True
                self.entry_file.set_text(
                    default_filename(FORMATS[self.format_key]["ext"])
                )
                self.updating_toggle = False

    def on_recorder_tick(self, _elapsed):
        self.update_recording_status()

    def on_recorder_error(self, title, message):
        self.set_status("Idle - " + title.lower())
        self.show_error(title, message)

    def on_recorder_finished(self, path, size, forced):
        name = os.path.basename(path or "")
        if forced:
            self.set_status("Idle - saved (forced) → " + name)
        else:
            self.set_status(
                "Idle - saved {0} ({1:.1f} MiB)".format(name, size / (1024.0 * 1024.0))
            )

    def update_recording_status(self):
        minutes, seconds = divmod(self.recorder.elapsed, 60)
        hours, minutes = divmod(minutes, 60)
        self.set_status(
            "Recording → {0}  [{1:02d}:{2:02d}:{3:02d}]".format(
                os.path.basename(self.recorder.path or ""), hours, minutes, seconds
            )
        )

    # -- shutdown -----------------------------------------------------------
    def shutdown(self):
        self.recorder.shutdown()


# -----------------------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------------------
USAGE = """Usage: system_audio_recorder.py [OPTION]

Records system audio output (what you hear) to WAV or Opus.
With no options, opens the recorder window.

  -V, --version    print the version and exit
      --about      print author, copyright and capture details, then exit
      --about-dialog
                   show the About window and exit
  -h, --help       show this message and exit
"""


def main():
    argv = sys.argv[1:]

    # Text-mode queries answer before any GUI or GStreamer setup happens.
    if "-h" in argv or "--help" in argv:
        sys.stdout.write(USAGE)
        return 0
    if "-V" in argv or "--version" in argv:
        print("{0} {1}".format(APP_TITLE, __version__))
        return 0
    if "--about" in argv:
        print(about_text())
        return 0

    unknown = [a for a in argv if a.startswith("-") and a != "--about-dialog"]
    if unknown:
        sys.stderr.write("Unknown option: {0}\n\n".format(unknown[0]))
        sys.stderr.write(USAGE)
        return 2

    # Match the .desktop file basename so the desktop shell can tie the
    # running window back to its launcher -- and therefore its icon.
    # Without this the app id would come from argv[0].
    GLib.set_prgname(APP_ID)
    GLib.set_application_name(APP_TITLE)

    Gst.init(None)

    if "--about-dialog" in argv:
        show_about_dialog()
        return 0

    required = [CAPTURE_ELEMENT, "audioconvert", "audioresample", "wavenc", "filesink"]
    if WINDOWS:
        required.append("audiorate")
    missing = [name for name in required if Gst.ElementFactory.find(name) is None]

    window = RecorderWindow()
    window.show_all()
    window.expander.set_expanded(False)

    if missing:
        window.show_error(
            "Missing GStreamer elements",
            "The following elements are not installed: {0}\n\n{1}".format(
                ", ".join(missing), INSTALL_HINT
            ),
        )
    elif MONITOR_WARNING:
        window.show_warning("Audio monitor source not detected", MONITOR_WARNING)
        window.expander.set_expanded(True)

    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())


# =============================================================================
#  VERIFICATION CHECKLIST
#  ---------------------------------------------------------------------------
#  1. Install the dependencies for your platform (see the top of this file).
#  2. python3 system_audio_recorder.py            -> small GTK window appears,
#     (Windows: python ...)                          status reads "Idle".
#  3. Click "Browse..." -> pick a folder/name; the extension is forced to match
#     the selected format (.wav or .opus). Switching the radio button rewrites
#     the extension in the entry.
#  4. Click "Start Recording" -> status becomes "Recording -> <file> [00:00:03]".
#     Play any audio through your speakers/headphones while it runs.
#  5. Click "Stop Recording" -> EOS is flushed, status returns to
#     "Idle - saved <file> (x.y MiB)".
#  6. Play the result:   vlc <file>       or      mpv <file>
#     Inspect it with:   gst-discoverer-1.0 <file>
#
#  Manual pipeline equivalents (handy for debugging outside the GUI):
#
#  LINUX:
#    gst-launch-1.0 pulsesrc device=$MONITOR ! audioconvert ! audioresample \
#        ! audio/x-raw,format=S16LE,rate=44100,channels=2 ! wavenc \
#        ! filesink location=/tmp/test.wav
#    gst-launch-1.0 pulsesrc device=$MONITOR ! audioconvert ! audioresample \
#        ! opusenc bitrate=128000 ! oggmux ! filesink location=/tmp/test.opus
#
#  WINDOWS (no device= needed: loopback follows the default output):
#    gst-launch-1.0 wasapi2src loopback=true ! audiorate ! audioconvert \
#        ! audioresample ! audio/x-raw,format=S16LE,rate=44100,channels=2 \
#        ! wavenc ! filesink location=test.wav
#    gst-launch-1.0 wasapi2src loopback=true ! audiorate ! audioconvert \
#        ! audioresample ! opusenc bitrate=128000 ! oggmux \
#        ! filesink location=test.opus
#
#  (Press Ctrl+C once; add -e so gst-launch sends EOS and finalises the file.)
#
#  Troubleshooting silence:
#    Linux   - the device must be a *.monitor* source. Verify with
#              pactl list short sources | grep monitor
#              and paste the right name into "Advanced -> Device".
#    Windows - leave "Advanced -> Device" EMPTY to follow the default output.
#              Note that players using WASAPI *exclusive* mode (some ASIO /
#              audiophile players) bypass the shared mixer and cannot be
#              captured by loopback at all.
# =============================================================================
