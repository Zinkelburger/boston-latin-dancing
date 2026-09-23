#!/usr/bin/env bash
# Install the weekly Claude review for this user: the bld-review systemd user
# timer + service, and the Boston Salsa tray app (app menu entry + autostart).
# Re-run any time; it overwrites its own files and keeps your schedule/settings.
#   desktop/install.sh            install / update
#   desktop/install.sh --remove   uninstall (logs and settings are kept)
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_DIR="$HOME/.config/systemd/user"
CONF_DIR="$HOME/.config/bld-review"
APPS_DIR="$HOME/.local/share/applications"
AUTOSTART_DIR="$HOME/.config/autostart"
ICON="$HOME/.local/share/icons/hicolor/512x512/apps/boston-salsa.png"

if [[ "${1:-}" == "--remove" ]]; then
  systemctl --user disable --now bld-review.timer 2>/dev/null || true
  pkill -f "^/usr/bin/python3 $REPO/desktop/bld_tray.py" 2>/dev/null || true
  rm -rf "$UNIT_DIR/bld-review.service" "$UNIT_DIR/bld-review.timer" "$UNIT_DIR/bld-review.timer.d"
  rm -f "$APPS_DIR/boston-salsa.desktop" "$AUTOSTART_DIR/boston-salsa.desktop" "$ICON"
  systemctl --user daemon-reload
  echo "Removed. Settings in $CONF_DIR and logs in automation/logs were kept."
  exit 0
fi

python3 -c 'import PySide6' 2>/dev/null || { echo "Needs PySide6: sudo dnf install python3-pyside6"; exit 1; }
command -v claude >/dev/null || { echo "Needs the claude CLI on PATH"; exit 1; }

mkdir -p "$UNIT_DIR" "$UNIT_DIR/bld-review.timer.d" "$CONF_DIR" "$APPS_DIR" "$AUTOSTART_DIR" "$(dirname "$ICON")"
chmod +x "$REPO/automation/claude_review.sh" "$REPO/desktop/bld_tray.py"

cat >"$UNIT_DIR/bld-review.service" <<EOF
# Installed by $REPO/desktop/install.sh
[Unit]
Description=Boston Salsa weekly review (refresh + Claude agent)
After=network-online.target

[Service]
Type=exec
WorkingDirectory=$REPO
Environment=BLD_REPO_DIR=$REPO
Environment=PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin
EnvironmentFile=-$CONF_DIR/env
ExecStart=$REPO/automation/claude_review.sh
TimeoutStartSec=infinity
RuntimeMaxSec=4h
Nice=5
EOF

cat >"$UNIT_DIR/bld-review.timer" <<EOF
# Installed by $REPO/desktop/install.sh. Day/time live in
# bld-review.timer.d/schedule.conf, which the tray app's Settings tab edits.
[Unit]
Description=Boston Salsa weekly review schedule

[Timer]
OnCalendar=Wed *-*-* 12:00:00 America/New_York
Persistent=true
Unit=bld-review.service

[Install]
WantedBy=timers.target
EOF

# Keep an existing schedule; seed the default (Wednesday noon) otherwise.
if [[ ! -f "$UNIT_DIR/bld-review.timer.d/schedule.conf" ]]; then
  cat >"$UNIT_DIR/bld-review.timer.d/schedule.conf" <<EOF
# Written by the Boston Salsa tray app (desktop/bld_tray.py).
[Timer]
OnCalendar=
OnCalendar=Wed *-*-* 12:00:00 America/New_York
Persistent=true
EOF
fi
[[ -f "$CONF_DIR/env" ]] || printf 'BLD_AGENT_MODEL=claude-opus-5-5\nBLD_SKIP_REFRESH=0\n' >"$CONF_DIR/env"

cp "$REPO/app/icon.png" "$ICON"
cat >"$APPS_DIR/boston-salsa.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Boston Salsa
Comment=Watch and schedule the weekly event review
Exec=/usr/bin/python3 $REPO/desktop/bld_tray.py --show
Icon=boston-salsa
Terminal=false
Categories=Utility;
StartupWMClass=boston-salsa
EOF
sed -e 's| --show||' -e '$a X-GNOME-Autostart-enabled=true' \
  "$APPS_DIR/boston-salsa.desktop" >"$AUTOSTART_DIR/boston-salsa.desktop"

systemctl --user daemon-reload
systemctl --user enable --now bld-review.timer

# (Re)start the tray app so it picks up any code changes.
pkill -f "^/usr/bin/python3 $REPO/desktop/bld_tray.py" 2>/dev/null || true
setsid -f /usr/bin/python3 "$REPO/desktop/bld_tray.py" >/dev/null 2>&1 </dev/null

echo "Installed. Next run:"
systemctl --user list-timers bld-review.timer --no-pager | sed -n 2p
