#!/usr/bin/env bash
# Install the desktop launcher, icon and AppStream metadata for the current
# user.  No root required.
#
#   ./packaging/install-desktop-entry.sh              install
#   ./packaging/install-desktop-entry.sh --uninstall  remove
#
# Author: lonestarparrot <lonestarparrot@gmail.com>
set -euo pipefail

APP_ID="io.github.lrash.SystemAudioRecorder"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
DESKTOP_FILE="$DATA/applications/$APP_ID.desktop"
ICON_FILE="$DATA/icons/hicolor/scalable/apps/$APP_ID.svg"
META_FILE="$DATA/metainfo/$APP_ID.metainfo.xml"

refresh() {
    command -v update-desktop-database >/dev/null && update-desktop-database "$DATA/applications" || true
    command -v gtk-update-icon-cache >/dev/null &&
        gtk-update-icon-cache -f -t "$DATA/icons/hicolor" >/dev/null 2>&1 || true
}

if [[ "${1:-}" == "--uninstall" ]]; then
    rm -fv "$DESKTOP_FILE" "$ICON_FILE" "$META_FILE"
    # Clean up the pre-0.3.0 unprefixed names too.
    rm -f "$DATA/applications/system-audio-recorder.desktop" \
          "$DATA/icons/hicolor/scalable/apps/system-audio-recorder.svg"
    refresh
    echo "Removed."
    exit 0
fi

mkdir -p "$(dirname "$DESKTOP_FILE")" "$(dirname "$ICON_FILE")" "$(dirname "$META_FILE")"
install -m 644 "$REPO/packaging/$APP_ID.svg" "$ICON_FILE"
install -m 644 "$REPO/packaging/$APP_ID.metainfo.xml" "$META_FILE"

# Exec carries this checkout's absolute path, so the entry keeps working
# wherever the repository lives.
cat > "$DESKTOP_FILE" <<DESKTOP
[Desktop Entry]
Type=Application
Version=1.0
Name=System Audio Recorder
GenericName=Audio Recorder
Comment=Record system audio output (what you hear) to WAV or Opus
Exec=python3 $REPO/system_audio_recorder.py
Path=$REPO
Icon=$APP_ID
Terminal=false
Categories=AudioVideo;Audio;Recorder;
Keywords=record;audio;sound;music;loopback;capture;monitor;stream;
StartupNotify=true
StartupWMClass=$APP_ID
X-GNOME-UsesNotifications=false
Actions=About;

[Desktop Action About]
Name=About / version
Exec=python3 $REPO/system_audio_recorder.py --about-dialog
DESKTOP
chmod 644 "$DESKTOP_FILE"

# Retire the pre-0.3.0 unprefixed entry so the menu shows one launcher.
rm -f "$DATA/applications/system-audio-recorder.desktop" \
      "$DATA/icons/hicolor/scalable/apps/system-audio-recorder.svg"

command -v desktop-file-validate >/dev/null && desktop-file-validate "$DESKTOP_FILE"
command -v appstreamcli >/dev/null && appstreamcli validate --no-net "$META_FILE" >/dev/null 2>&1 || true
refresh
echo "Installed: $DESKTOP_FILE"
echo "Look for \"System Audio Recorder\" in your applications menu."
