# System Audio Recorder

A small GTK app for Ubuntu that records **what you hear** — the system audio
output (loopback "monitor" source) — to WAV or Opus. Useful for capturing
music, streams, or anything else playing through your speakers or headphones.

Pure GStreamer + PyGObject. No FFmpeg, no subprocess recording. A single
Python file with no pip dependencies — everything comes from apt.

## Features

- Records the system output monitor source (not the microphone)
- WAV (lossless) or Opus (~128 kbps) output
- Auto-detects the right monitor device on PulseAudio **and** PipeWire —
  works via `pactl` when available, falls back to `pw-dump` on pure
  PipeWire systems without pulseaudio-utils
- Timestamped default filenames (`~/sysrec-YYYYMMDD-HHMMSS.wav`), fresh for
  every take unless you pick a name yourself
- Optional auto-stop timer, device override under "Advanced"
- Clean EOS finalisation so files are always playable — including when you
  close the window mid-recording

## Install

```bash
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0 \
    gstreamer1.0-plugins-good gstreamer1.0-libav \
    gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly
```

## Run

```bash
python3 system_audio_recorder.py
```

Pick a format, optionally choose a filename, press **Start Recording**, play
your audio, press **Stop Recording**.

## Troubleshooting

**The recording is silent.** The capture device must be a `.monitor` source
(the loopback of an output), not a microphone. List the candidates with
either of:

```bash
pactl list short sources | grep monitor
```

```bash
pw-cli ls Node | grep -i monitor
```

then paste the right name into **Advanced → Device**.

**"No element ..." error on start.** A GStreamer plugin package is missing —
re-run the apt command above. Element origins are documented at the top of
the script.

A fuller manual verification checklist (including `gst-launch-1.0`
equivalents of both pipelines for debugging outside the GUI) is at the
bottom of the script.
