# System Audio Recorder

A small GTK app that records **what you hear** — the system audio output
loopback — to WAV or Opus. Useful for capturing music, streams, or anything
else playing through your speakers or headphones.

Runs on **Linux** (PulseAudio or PipeWire) and **Windows 10/11** (WASAPI
loopback). Pure GStreamer + PyGObject: no FFmpeg, no subprocess recording, a
single Python file with no pip dependencies.

## Platform status

| Platform | Status |
|---|---|
| Linux — PulseAudio / PipeWire | **Verified.** Both automated suites pass on Ubuntu with PipeWire. |
| Windows 10/11 — WASAPI loopback | **Implemented, not yet run on Windows hardware.** The code path, install docs and test kit are complete, and everything testable from Linux passes — but `wasapi2src` itself has never executed. Treat as beta until the Phase 4 matrix in [`ref_docs/WINDOWS-PORT-PLAN.md`](ref_docs/WINDOWS-PORT-PLAN.md) is run. |

## Features

- Records the system **output** (not the microphone)
- WAV (lossless) or Opus (~128 kbps)
- Finds the right capture device by itself on every supported platform:
  - Linux — auto-detects the monitor source via `pactl`, falling back to
    `pw-dump` on pure PipeWire systems without pulseaudio-utils
  - Windows — `wasapi2src` follows the default output device, no setup
- Timestamped default filenames (`sysrec-YYYYMMDD-HHMMSS.wav` in your home
  folder), fresh for every take unless you pick a name yourself
- Optional auto-stop timer, device override under "Advanced"
- Clean EOS finalisation so files are always playable — including when you
  close the window mid-recording

## Install

### Linux (Debian/Ubuntu)

```bash
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0 \
    gstreamer1.0-plugins-good gstreamer1.0-libav \
    gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly
```

### Windows 10/11

Install [MSYS2](https://www.msys2.org/), then from the **UCRT64** shell:

```bash
pacman -S --needed mingw-w64-ucrt-x86_64-python mingw-w64-ucrt-x86_64-python-gobject mingw-w64-ucrt-x86_64-gtk3 mingw-w64-ucrt-x86_64-gstreamer mingw-w64-ucrt-x86_64-gst-plugins-base mingw-w64-ucrt-x86_64-gst-plugins-good mingw-w64-ucrt-x86_64-gst-plugins-bad
```

`wasapi2src` lives in gst-plugins-bad and needs GStreamer ≥ 1.18; MSYS2 is
well past that.

## Run

```bash
python3 system_audio_recorder.py
```

### Add it to your applications menu (Linux)

```bash
./packaging/install-desktop-entry.sh
```

Puts "System Audio Recorder" in the applications menu with a music icon, so
you can launch it without a terminal. It writes a `.desktop` entry and icon
under `~/.local/share` — no root required — using this checkout's path, so it
keeps working wherever the repo lives. `--uninstall` removes both.

On Windows, from the UCRT64 shell, use `python system_audio_recorder.py` — or
`pythonw system_audio_recorder.py` to launch without a console window.

Pick a format, optionally choose a filename, press **Start Recording**, play
your audio, press **Stop Recording**.

## Troubleshooting

**The recording is silent.**

On **Linux**, the capture device must be a `.monitor` source (the loopback of
an output), not a microphone. List the candidates with either of:

```bash
pactl list short sources | grep monitor
```

```bash
pw-cli ls Node | grep -i monitor
```

then paste the right name into **Advanced → Device**.

On **Windows**, leave **Advanced → Device** *empty* so capture follows the
default output device. To target a specific device, list them with
`gst-device-monitor-1.0 Audio/Sink`. Note that players using WASAPI
**exclusive mode** (some ASIO and audiophile players) bypass the shared mixer
and cannot be captured by loopback at all — that is a Windows limitation, not
a bug in this app.

**"No element ..." error on start.** A GStreamer plugin package is missing —
re-run the install command for your platform. Element origins are documented
at the top of the script.

## Architecture

The file is one script but two layers, and the split is the load-bearing part
of the design:

- **`Recorder`** — the engine. Owns the GStreamer pipeline, the
  `idle → recording → stopping → idle` state machine, and all the timers. It
  uses GLib but never Gtk, holds its own path/format/elapsed as real
  attributes, and reports progress through four callbacks (`on_state`,
  `on_tick`, `on_error`, `on_finished`). It can be driven with no GUI at all.
- **`RecorderWindow`** — the view. Builds widgets, resolves the output path
  and format from them, calls `recorder.start()` / `recorder.stop()`, and
  renders the callbacks into the status label and button states.

Because the engine has no widget dependency, a second front-end (a CLI, a tray
icon, a web UI) can reuse it whole rather than re-implementing the delicate
part — EOS finalisation, timeout guards, teardown ordering.

## Verifying it works

Two headless harnesses, both exit 0 on success:

```bash
python3 ref_docs/verify_recorder.py
```

End-to-end audio check: plays a test tone while recording the loopback through
the app's own pipeline code, then asserts both files decode and actually
contain the tone. Add `--keep` to keep the recordings, `--seconds N` to record
longer.

```bash
python3 ref_docs/verify_engine.py
```

Engine test suite: drives `Recorder` directly with no window — full cycle with
auto-stop, manual stop, callback ordering, error paths, mid-recording shutdown,
and the re-entrancy guard.

A fuller manual checklist (including `gst-launch-1.0` equivalents of both
pipelines, per platform, for debugging outside the GUI) is at the bottom of
the script.

## Project notes

`ref_docs/WINDOWS-PORT-PLAN.md` documents the Windows port design, the
approaches considered, remaining risks, and the on-hardware test matrix.

## Checking version, author and copyright

```bash
python3 system_audio_recorder.py --about
```

Prints the author, copyright, source, signing key and the capture device in
use. `--version` gives just the version; `--help` lists the options.

In the GUI, click **About** at the bottom-right of the window. You can also
right-click the launcher in the applications menu and choose **About /
version**.

Note that Linux does not embed publisher details inside files the way Windows
`.exe` files do, so Files' Properties dialog shows only size and permissions —
the commands above are the equivalent.

## Author & integrity

Created by **lonestarparrot** — <lonestarparrot@gmail.com>
Copyright (c) 2026 lonestarparrot. All rights reserved.

Releases are signed with OpenPGP key `53CAF98A8019832F911E3BA653D4586CB4F47C1D`
(short id `53D4586CB4F47C1D`). To check that a copy is authentic and unmodified:

```bash
gpg --import packaging/lonestarparrot-signing-key.asc
gpg --verify SHA256SUMS.asc SHA256SUMS && sha256sum -c SHA256SUMS
```

A good signature plus matching checksums means every file is exactly as
published. Git tags and commits are signed with the same key, so GitHub shows
them as **Verified**. Re-sign after changes with
`./packaging/sign-release.sh`.

Attribution the desktop reads is in
`packaging/io.github.lrash.SystemAudioRecorder.metainfo.xml` (AppStream), which
names lonestarparrot as the developer.

> Note on "trust": Linux has no OS-level publisher gate for scripts or desktop
> launchers, so nothing here suppresses a warning the way a Windows
> Authenticode certificate does — there is no such warning to suppress. What
> the signature gives you is verifiable authorship and tamper-evidence.
