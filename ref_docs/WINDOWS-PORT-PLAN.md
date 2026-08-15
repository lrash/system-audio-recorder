# Windows Port Plan — System Audio Recorder

*Drafted 2026-08-15. Updated 2026-08-15 after implementation.*

## Status

| Phase | State |
|---|---|
| 1 — Platform abstraction refactor | **DONE**, Linux regression gate PASSED |
| 2 — Windows capture specifics | **DONE** (device tooltip/placeholder, pythonw note) |
| 3 — Install & run documentation | **DONE** (README) |
| 4 — Windows verification on hardware | **BLOCKED — needs a real Windows machine** |
| 5 — Packaging | not started (optional) |

What could be proven on Linux has been:

- Linux pipeline strings are **byte-identical** to the pre-refactor versions.
- Linux end-to-end gate (tone → record → decode + peak check) **PASS**.
- GUI smoke test (build window, toggle formats, rewrite extensions) **OK**.
- Windows branch resolved by forcing `sys.platform` — correct source element,
  empty device, no subprocess calls, `%USERPROFILE%` honoured.
- Windows **encode chain** (`audiorate ! audioconvert ! … ! wavenc/opusenc`)
  run for real with a stand-in source: both files decode.

The one thing Linux cannot prove is `wasapi2src` itself. That is Phase 4.

### Deviation from the original plan

`low-latency=true` was **not** set on `wasapi2src`. For a recorder, glitch-free
capture beats a snappier stop, and EOS finalisation already bounds stop time.
`audiorate` **was** added to the Windows chain up front (rather than held as a
fallback) because it is an inert passthrough when the source behaves and fixes
risk 1 when it does not — cheap insurance, verified harmless.

## Goal

Make `system_audio_recorder.py` record system audio output ("what you hear")
on Windows 10/11, keeping the single-file, no-pip-dependency character of the
Linux version. Non-goals for the first pass: installer/EXE packaging, per-app
capture, microphone mixing.

## Why the current architecture ports well

The app is GTK3 + GStreamer via PyGObject. Both stacks are fully supported on
Windows (via MSYS2). Everything downstream of the capture source —
`audioconvert ! audioresample ! wavenc/opusenc`, the EOS finalisation logic,
the GLib timers, the whole GUI — is already platform-neutral. The only two
Linux-specific pieces are:

1. **The source element**: `pulsesrc device=<sink>.monitor` (PulseAudio/PipeWire
   loopback).
2. **Device detection**: shelling out to `pactl` / `pw-dump`.

On Windows the equivalent of a monitor source is **WASAPI loopback capture**,
and GStreamer wraps it natively: `wasapi2src loopback=true` (gst-plugins-bad,
GStreamer ≥ 1.18; MSYS2 ships ≥ 1.24). With no `device` property set it
captures the **default render device** — which is exactly the "record what I
hear" default, with zero external tooling. So the Windows path needs *less*
detection code than Linux, not more.

## Approaches considered

| | Approach | Verdict |
|---|---|---|
| **A** | **Same codebase, GTK3 + GStreamer under MSYS2; swap source element per platform** | **Recommended.** ~60–100 lines of platform layer; one file stays canonical; Opus encoding comes free. Cost: users install MSYS2 packages (documented one-liner). |
| B | Official GStreamer MSI + rewrite GUI in tkinter | Avoids MSYS2, but the MSI's Python bindings are version-locked and fragile, and we'd maintain two GUIs. |
| C | Pure-pip rewrite (`pyaudiowpatch`/`soundcard` + `wave`) | Easiest end-user install, but a second codebase, loses Opus (needs an encoder dep), loses the proven GStreamer EOS/finalisation behavior. |

## Work plan

### Phase 1 — Platform abstraction refactor (Linux-safe, do first)

- Add `PLATFORM = "windows" if sys.platform == "win32" else "linux"`.
- Split `FORMATS[...]["description"]` into a shared encode chain
  (`audioconvert ! audioresample ! ... ! filesink name=sink`) plus a
  per-platform source fragment:
  - linux: `pulsesrc name=src`
  - windows: `wasapi2src name=src loopback=true low-latency=true`
- `build_pipeline()`: set the `device` property **only when an override is
  given**; on Windows the default (no device) = default render device.
- `detect_monitor()`: on Windows return `(None, None)` — no subprocess calls,
  no warning dialog. Keep the pactl → pw-dump chain on Linux unchanged.
- Startup element check: `pulsesrc` on Linux, `wasapi2src` on Windows.
- Default filename dir: on Windows use `%USERPROFILE%` explicitly —
  under MSYS2 Python, `os.path.expanduser("~")` resolves to the MSYS2 home
  (`C:\msys64\home\<user>`), not the Windows profile.
- **Regression gate**: rerun the Linux headless verify (tone → record both
  formats → decode + peak check) and confirm PASS before touching Windows.

### Phase 2 — Windows capture specifics

- Advanced → Device stays a free-text override; on Windows it takes a WASAPI
  device string. Populate the tooltip with how to list devices:
  `gst-device-monitor-1.0 Audio/Sink` (ships with gstreamer).
- Stretch: use `Gst.DeviceMonitor` (cross-platform, no subprocess) to offer a
  dropdown of render devices on both OSes — this would eventually let us
  delete the pactl/pw-dump code entirely.
- Add a `system_audio_recorder.pyw` launcher note (or `pythonw` invocation)
  so no console window appears.

### Phase 3 — Install & run documentation (README section)

MSYS2 UCRT64 environment, one command:

```bash
pacman -S --needed \
  mingw-w64-ucrt-x86_64-python \
  mingw-w64-ucrt-x86_64-python-gobject \
  mingw-w64-ucrt-x86_64-gtk3 \
  mingw-w64-ucrt-x86_64-gstreamer \
  mingw-w64-ucrt-x86_64-gst-plugins-base \
  mingw-w64-ucrt-x86_64-gst-plugins-good \
  mingw-w64-ucrt-x86_64-gst-plugins-bad
```

Run from the UCRT64 shell: `python system_audio_recorder.py`.

### Phase 4 — Windows verification (on real hardware, non-negotiable)

`ref_docs/verify_recorder.py` is already cross-platform: it picks
`wasapi2sink` for tone playback on Windows and drives the app's own
`build_pipeline()`, so it exercises the real Windows chain, not a copy.
**A Linux run is never Windows verification.**

#### Copy-paste for the Windows machine

Install MSYS2 from https://www.msys2.org/, then in the **UCRT64** shell:

```bash
pacman -S --needed git mingw-w64-ucrt-x86_64-python mingw-w64-ucrt-x86_64-python-gobject mingw-w64-ucrt-x86_64-gtk3 mingw-w64-ucrt-x86_64-gstreamer mingw-w64-ucrt-x86_64-gst-plugins-base mingw-w64-ucrt-x86_64-gst-plugins-good mingw-w64-ucrt-x86_64-gst-plugins-bad
```

Get the code. The repo is **private**, so the clone needs to authenticate as
the `lrash` account. The path of least resistance is to clone with **Git for
Windows** (its credential manager can log in through the browser), into a
normal Windows folder, and only then work on it from the MSYS2 shell — MSYS2's
own `git` has no credential helper and will ask for a password that a GitHub
account no longer accepts. From PowerShell or Command Prompt:

```powershell
git clone https://github.com/lrash/system-audio-recorder.git %USERPROFILE%\system-audio-recorder
```

Then in the MSYS2 **UCRT64** shell, that folder is visible under `/c/Users/`:

```bash
cd /c/Users/$USERNAME/system-audio-recorder
```

Everything needed is on `main` — no branch switch. Confirm with
`git log --oneline -1`, which should show the merge commit.

Confirm the loopback element exists and the plugin version is new enough:

```bash
gst-inspect-1.0 wasapi2src | head -20
```

Start some audio playing, then run the automated gate:

```bash
python ref_docs/verify_recorder.py --seconds 6 --keep
```

Expect `RESULT   : PASS`, a WAV peak well above 500, and both durations ≈ 6 s.
A PASS with a near-zero peak means loopback captured silence — check that
audio was actually playing to the *default* output device.

Then the GUI:

```bash
python system_audio_recorder.py
```

#### Manual test matrix on the Windows box

- [ ] Default device capture (speakers) — WAV and Opus
- [ ] Capture while default output is a USB/Bluetooth headset
- [ ] Switch the default output device *while idle*, record again — follows it
- [ ] Recording spans a silence gap (see risk 1) — duration must match
      wall-clock, not just the audible portion
- [ ] Stop via button, auto-stop timer, and window-close mid-recording —
      file plays in all three cases
- [ ] Save path with spaces, and a OneDrive-synced folder
- [ ] Default filename lands in the **Windows profile** (`C:\Users\<name>`),
      not the MSYS2 home — this is the `home_directory()` fix
- [ ] Device override via Advanced field (id from
      `gst-device-monitor-1.0 Audio/Sink`)

### Phase 5 (optional, later) — Packaging

PyInstaller onefile with GTK+GStreamer hooks, or a zipped MSYS2 runtime
subset. Fiddly; only worth it if the app gets non-technical users.

## Risks

1. **WASAPI loopback silence behavior**: classic WASAPI loopback only
   delivers buffers while something is rendering; silence can appear as a
   *gap* (shorter file) rather than zero-samples. `wasapi2src` mitigates on
   Win10+; must be confirmed in Phase 4's silence-gap test. Fallback: insert
   `audiorate` before the encoder on Windows to fill gaps.
2. **`wasapi2src` vs older `wasapisrc`**: both exist in plugins-bad; wasapi2
   is the maintained one. Pin the element check to `wasapi2src` and require
   GStreamer ≥ 1.18 (MSYS2 is far past this).
3. **GTK3 look on Windows**: functional but non-native (file dialogs
   included). Cosmetic; accept for v1.
4. **Exclusive-mode audio apps** (ASIO/WASAPI-exclusive players) bypass the
   shared mixer — loopback records silence from them. Document as a known
   limitation.

## Effort estimate

Phases 1–3: one focused session on this box (Linux regression gate included).
Phase 4: ~30–45 min on the Windows machine.

## Pre-work checklist for the implementing session

- Repo: `~/Scripts/system-audio-recorder` → github.com/lrash/system-audio-recorder
  (PRIVATE, personal account). `gh auth switch --user lrash` before pushing,
  back to `lrashhcad-hub` after.
- Branch the port work (`windows-port`), merge after Phase 4 passes.
