#!/usr/bin/env bash
# Install the desktop launcher + icon for the current user (no root needed).
#
#   ./packaging/install-desktop-entry.sh
#
# Adds "System Audio Recorder" to the applications menu with a music icon.
# Pass --uninstall to remove both again.
set -euo pipefail

APP_ID="system-audio-recorder"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DESKTOP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICON_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
DESKTOP_FILE="$DESKTOP_DIR/$APP_ID.desktop"
ICON_FILE="$ICON_DIR/$APP_ID.svg"

refresh() {
    command -v update-desktop-database >/dev/null && update-desktop-database "$DESKTOP_DIR" || true
    command -v gtk-update-icon-cache >/dev/null &&
        gtk-update-icon-cache -f -t "${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor" >/dev/null 2>&1 || true
}

if [[ "${1:-}" == "--uninstall" ]]; then
    rm -fv "$DESKTOP_FILE" "$ICON_FILE"
    refresh
    echo "Removed."
    exit 0
fi

mkdir -p "$DESKTOP_DIR" "$ICON_DIR"
install -m 644 "$REPO/packaging/$APP_ID.svg" "$ICON_FILE"

# Exec is written with this checkout's absolute path, so the entry keeps
# working regardless of where the repo lives.
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
DESKTOP
chmod 644 "$DESKTOP_FILE"

command -v desktop-file-validate >/dev/null && desktop-file-validate "$DESKTOP_FILE"
refresh
echo "Installed: $DESKTOP_FILE"
echo "Look for \"System Audio Recorder\" in your applications menu."
