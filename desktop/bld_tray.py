#!/usr/bin/env python3
"""Boston Salsa tray app: watch, run and schedule the weekly Claude review.

Sits in the system tray with the site logo. The review itself is the
bld-review.service systemd user unit (automation/claude_review.sh), started by
bld-review.timer — this app only watches it, so a run happens whether or not
the app is open. When a run starts or finishes a notification pops up; click
it (or the tray icon) to see what the agent is doing, read the last summary,
browse earlier runs, or change the schedule and model.

Install with desktop/install.sh; see desktop/README.md.
"""
from __future__ import annotations

import html
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTime, QTimer, QUrl
from PySide6.QtGui import QAction, QColor, QDesktopServices, QIcon, QPainter, QPixmap
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLabel,
    QMainWindow, QMenu, QMessageBox, QPushButton, QSystemTrayIcon, QTabWidget,
    QTextBrowser, QTimeEdit, QVBoxLayout, QWidget,
)

REPO = Path(__file__).resolve().parent.parent
LOG_DIR = REPO / "automation" / "logs"
SUMMARY = LOG_DIR / "last-agent-summary.md"
LOGO = REPO / "app" / "icon.png"
SITE_URL = "https://bostonsalsa.org"

SERVICE = "bld-review.service"
TIMER = "bld-review.timer"
UNIT_DIR = Path.home() / ".config" / "systemd" / "user"
SCHEDULE_DROPIN = UNIT_DIR / f"{TIMER}.d" / "schedule.conf"
ENV_FILE = Path.home() / ".config" / "bld-review" / "env"

APP_ID = "boston-salsa"
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MODELS = ["claude-opus-5-5", "claude-sonnet-5", "claude-fable-5-1", "claude-haiku-4-5-20251001"]
DEFAULT_SCHEDULE = {"days": ["Wed"], "time": "12:00", "persistent": True}
DEFAULT_ENV = {"BLD_AGENT_MODEL": "claude-opus-5-5", "BLD_SKIP_REFRESH": "0"}


# ---------------------------------------------------------------- systemd

def systemctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True)


def unit_props(unit: str, *props: str) -> dict[str, str]:
    out = systemctl("show", unit, *(f"--property={p}" for p in props)).stdout
    return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)


def read_schedule() -> dict:
    sched = dict(DEFAULT_SCHEDULE)
    try:
        text = SCHEDULE_DROPIN.read_text()
    except OSError:
        return sched
    for m in re.finditer(r"^OnCalendar=(\S+) \*-\*-\* (\d\d:\d\d)", text, re.M):
        sched["days"] = [d for d in m.group(1).split(",") if d in DAYS]
        sched["time"] = m.group(2)
    m = re.search(r"^Persistent=(\w+)", text, re.M)
    if m:
        sched["persistent"] = m.group(1).lower() in ("true", "yes", "1")
    return sched


def write_schedule(days: list[str], time: str, persistent: bool) -> None:
    SCHEDULE_DROPIN.parent.mkdir(parents=True, exist_ok=True)
    SCHEDULE_DROPIN.write_text(
        "# Written by the Boston Salsa tray app (desktop/bld_tray.py).\n"
        "[Timer]\n"
        "OnCalendar=\n"
        f"OnCalendar={','.join(days)} *-*-* {time}:00 America/New_York\n"
        f"Persistent={'true' if persistent else 'false'}\n"
    )
    systemctl("daemon-reload")
    if systemctl("is-active", TIMER).stdout.strip() == "active":
        systemctl("restart", TIMER)


def read_env() -> dict[str, str]:
    env = dict(DEFAULT_ENV)
    try:
        for line in ENV_FILE.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    except OSError:
        pass
    return env


def write_env(env: dict[str, str]) -> None:
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    ENV_FILE.write_text("".join(f"{k}={v}\n" for k, v in env.items()))


# ---------------------------------------------------------------- run log

def run_logs() -> list[Path]:
    return sorted(LOG_DIR.glob("review-2*.jsonl"), reverse=True)


def short(text: str, n: int = 160) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def tool_label(name: str, args: dict) -> str:
    name = name.removeprefix("mcp__boston-latin-dance__")
    if name == "Bash":
        return f"$ {short(args.get('command', ''), 140)}"
    for key in ("file_path", "url", "query", "pattern"):
        if key in args:
            return f"{name}  {short(args[key], 120)}"
    shown = ", ".join(f"{k}={short(v, 40)}" for k, v in args.items() if k != "updates_json")
    return f"{name}({short(shown, 120)})"


def result_text(content) -> str:
    if isinstance(content, list):
        return " ".join(c.get("text", "") for c in content if isinstance(c, dict))
    return str(content or "")


class RunState:
    """What one run's log says: rendered HTML lines plus a status summary."""

    def __init__(self) -> None:
        self.phase = "idle"
        self.started: datetime | None = None
        self.tool_calls = 0
        self.last_action = ""
        self.exit_code: int | None = None
        self.result: dict | None = None
        self.mcp_problem = ""

    def render(self, raw: str) -> str:
        """Fold one log line into the state and return it as HTML ('' to skip)."""
        raw = raw.rstrip("\n")
        if not raw.strip():
            return ""
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            return f'<div style="color:gray">{html.escape(short(raw, 300))}</div>'
        if not isinstance(ev, dict):
            return ""
        t = ev.get("type")

        if t == "bld":
            kind, text = ev.get("kind"), str(ev.get("text", ""))
            if kind == "phase":
                if text == "start":
                    try:
                        self.started = datetime.fromisoformat(ev.get("ts", ""))
                    except ValueError:
                        self.started = None
                self.phase = text
                label = {"start": "Run started", "refresh": "Scrape & refresh",
                         "agent": "Claude review", "done": "Finished"}.get(text, text)
                return f'<h3 style="margin:10px 0 2px 0">{html.escape(label)}</h3>'
            if kind == "refresh":
                self.last_action = short(text, 80)
                return f'<div style="color:gray;font-family:monospace">{html.escape(text)}</div>'
            if kind == "warning":
                return f'<div style="color:#d97706">⚠ {html.escape(text)}</div>'
            if kind == "exit":
                self.exit_code = int(text) if text.lstrip("-").isdigit() else None
                ok = self.exit_code == 0
                return (f'<div style="color:{"#16a34a" if ok else "#dc2626"}"><b>'
                        f'{"✓ Done" if ok else f"✗ Exited with code {html.escape(text)}"}</b></div>')
            return f"<div>{html.escape(text)}</div>"

        if t == "system" and ev.get("subtype") == "init":
            servers = ev.get("mcp_servers") or []
            bad = [s.get("name", "?") for s in servers if s.get("status") != "connected"]
            n_mcp = sum(1 for tool in ev.get("tools", []) if str(tool).startswith("mcp__"))
            self.mcp_problem = f"MCP server not connected: {', '.join(bad)}" if bad else ""
            line = f"Claude session · {html.escape(str(ev.get('model', '')))} · {n_mcp} site tools"
            if bad:
                line += f' · <span style="color:#dc2626">{html.escape(self.mcp_problem)}</span>'
            return f'<div style="color:gray">{line}</div>'

        if t == "assistant":
            parts = []
            for block in (ev.get("message") or {}).get("content", []):
                if block.get("type") == "text" and block.get("text", "").strip():
                    body = html.escape(block["text"].strip()).replace("\n", "<br>")
                    parts.append(f'<div style="margin:6px 0">{body}</div>')
                elif block.get("type") == "tool_use":
                    self.tool_calls += 1
                    label = tool_label(block.get("name", "?"), block.get("input") or {})
                    self.last_action = short(label, 80)
                    parts.append(f'<div style="color:#2563eb;font-family:monospace">▸ {html.escape(label)}</div>')
            return "".join(parts)

        if t == "user":
            parts = []
            for block in (ev.get("message") or {}).get("content", []):
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    text = short(result_text(block.get("content")), 200)
                    if block.get("is_error"):
                        parts.append(f'<div style="color:#dc2626;font-family:monospace">&nbsp;&nbsp;↳ ✗ {html.escape(text)}</div>')
                    elif text:
                        parts.append(f'<div style="color:gray;font-family:monospace">&nbsp;&nbsp;↳ {html.escape(text)}</div>')
            return "".join(parts)

        if t == "result":
            self.result = ev
            mins = round((ev.get("duration_ms") or 0) / 60000)
            bits = [str(ev.get("subtype", "")), f"{ev.get('num_turns', '?')} turns", f"{mins} min"]
            if ev.get("total_cost_usd") is not None:
                bits.append(f"${ev['total_cost_usd']:.2f}")
            return f'<div style="margin-top:6px"><b>Claude finished</b> · {html.escape(" · ".join(bits))}</div>'
        return ""

    def status_line(self) -> str:
        if self.phase in ("idle", "done") or self.started is None:
            return ""
        mins = int((datetime.now().astimezone() - self.started).total_seconds() // 60)
        phase = {"refresh": "scraping", "agent": "Claude reviewing"}.get(self.phase, self.phase)
        line = f"{phase} · {mins} min"
        if self.phase == "agent":
            line += f" · {self.tool_calls} steps"
        return line


# ---------------------------------------------------------------- icons

def badge_icon(base: QPixmap, color: str | None) -> QIcon:
    if color is None:
        return QIcon(base)
    pm = base.scaled(QSize(64, 64), Qt.KeepAspectRatio, Qt.SmoothTransformation)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QColor("white"))
    p.setBrush(QColor(color))
    p.drawEllipse(38, 38, 24, 24)
    p.end()
    return QIcon(pm)


# ---------------------------------------------------------------- window

class MainWindow(QMainWindow):
    def __init__(self, app: "TrayApp") -> None:
        super().__init__()
        self.app = app
        self.setWindowTitle("Boston Salsa — weekly review")
        self.setWindowIcon(app.logo_icon)
        self.resize(900, 680)

        root = QWidget()
        layout = QVBoxLayout(root)

        head = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(app.logo.scaled(48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        head.addWidget(logo)
        self.status = QLabel()
        self.status.setTextFormat(Qt.RichText)
        head.addWidget(self.status, 1)
        self.run_btn = QPushButton("Run now")
        self.run_btn.clicked.connect(app.run_now)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(app.stop_run)
        site_btn = QPushButton("Open site")
        site_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(SITE_URL)))
        logs_btn = QPushButton("Log folder")
        logs_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(LOG_DIR))))
        for b in (self.run_btn, self.stop_btn, site_btn, logs_btn):
            head.addWidget(b)
        layout.addLayout(head)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.setCentralWidget(root)

        # Activity
        act = QWidget()
        act_l = QVBoxLayout(act)
        pick = QHBoxLayout()
        pick.addWidget(QLabel("Run:"))
        self.run_pick = QComboBox()
        self.run_pick.currentIndexChanged.connect(self._pick_changed)
        pick.addWidget(self.run_pick, 1)
        act_l.addLayout(pick)
        self.activity = QTextBrowser()
        self.activity.setOpenExternalLinks(True)
        act_l.addWidget(self.activity, 1)
        self.tabs.addTab(act, "Activity")

        # Summary
        self.summary = QTextBrowser()
        self.summary.setOpenExternalLinks(True)
        self.tabs.addTab(self.summary, "Last summary")

        # Settings
        self.tabs.addTab(self._settings_tab(), "Schedule && settings")

        self.tabs.currentChanged.connect(lambda i: i == 1 and self.load_summary())
        self.refresh_runs()

    # -- activity

    def refresh_runs(self) -> None:
        current = self.run_pick.currentData()
        self.run_pick.blockSignals(True)
        self.run_pick.clear()
        self.run_pick.addItem("Latest run (follow live)", None)
        for path in run_logs()[:50]:
            stamp = path.stem.removeprefix("review-")
            try:
                label = datetime.strptime(stamp, "%Y%m%d-%H%M%S").strftime("%a %b %d %Y, %I:%M %p")
            except ValueError:
                label = stamp
            self.run_pick.addItem(label, str(path))
        idx = self.run_pick.findData(current)
        self.run_pick.setCurrentIndex(max(idx, 0))
        self.run_pick.blockSignals(False)

    def _pick_changed(self) -> None:
        path = self.run_pick.currentData()
        if path is None:
            self.app.reload_log()
            return
        state = RunState()
        with open(path, encoding="utf-8", errors="replace") as f:
            body = "".join(state.render(line) for line in f)
        self.activity.setHtml(body or "<i>Empty log.</i>")

    def following(self) -> bool:
        return self.run_pick.currentData() is None

    def append_activity(self, chunk: str) -> None:
        if not self.following() or not chunk:
            return
        bar = self.activity.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 8
        self.activity.append(chunk)
        if at_bottom:
            bar.setValue(bar.maximum())

    def load_summary(self) -> None:
        try:
            md = SUMMARY.read_text(encoding="utf-8")
            when = datetime.fromtimestamp(SUMMARY.stat().st_mtime).strftime("%a %b %d, %I:%M %p")
            self.summary.setMarkdown(f"*Written {when}*\n\n{md}")
        except OSError:
            self.summary.setMarkdown("*No summary yet — one is written at the end of each run.*")

    # -- settings

    def _settings_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)

        self.enabled = QCheckBox("Run the review automatically on this schedule")
        form.addRow(self.enabled)

        days_row = QHBoxLayout()
        self.day_boxes = {}
        for d in DAYS:
            cb = QCheckBox(d)
            self.day_boxes[d] = cb
            days_row.addWidget(cb)
        days_row.addStretch(1)
        form.addRow("Days:", days_row)

        self.time_edit = QTimeEdit()
        self.time_edit.setDisplayFormat("h:mm AP")
        form.addRow("Time (Boston):", self.time_edit)

        self.persistent = QCheckBox("If the computer was off or asleep, run as soon as it's back")
        form.addRow(self.persistent)

        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.addItems(MODELS)
        form.addRow("Model:", self.model)

        self.do_refresh = QCheckBox("Scrape all sources first (refresh.sh)")
        form.addRow(self.do_refresh)

        self.next_run = QLabel()
        form.addRow("Next run:", self.next_run)

        save = QPushButton("Save")
        save.clicked.connect(self.save_settings)
        form.addRow(save)

        note = QLabel(
            "The run itself is the <code>bld-review</code> systemd user timer, so it happens "
            "even when this window or the tray app is closed. Claude runs unattended with "
            "permission checks off and can push to main."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: gray")
        form.addRow(note)
        self.load_settings()
        return w

    def load_settings(self) -> None:
        sched, env = read_schedule(), read_env()
        self.enabled.setChecked(systemctl("is-enabled", TIMER).stdout.strip() == "enabled")
        for d, cb in self.day_boxes.items():
            cb.setChecked(d in sched["days"])
        self.time_edit.setTime(QTime.fromString(sched["time"], "HH:mm"))
        self.persistent.setChecked(sched["persistent"])
        self.model.setCurrentText(env.get("BLD_AGENT_MODEL", DEFAULT_ENV["BLD_AGENT_MODEL"]))
        self.do_refresh.setChecked(env.get("BLD_SKIP_REFRESH", "0") != "1")

    def save_settings(self) -> None:
        days = [d for d, cb in self.day_boxes.items() if cb.isChecked()]
        if not days:
            QMessageBox.warning(self, "Boston Salsa", "Pick at least one day.")
            return
        write_schedule(days, self.time_edit.time().toString("HH:mm"), self.persistent.isChecked())
        env = read_env()
        env["BLD_AGENT_MODEL"] = self.model.currentText().strip() or DEFAULT_ENV["BLD_AGENT_MODEL"]
        env["BLD_SKIP_REFRESH"] = "0" if self.do_refresh.isChecked() else "1"
        write_env(env)
        r = systemctl("enable" if self.enabled.isChecked() else "disable", "--now", TIMER)
        if r.returncode != 0:
            QMessageBox.warning(self, "Boston Salsa", r.stderr or "systemctl failed")
        self.app.poll()

    def closeEvent(self, event) -> None:  # closing hides; the tray keeps running
        event.ignore()
        self.hide()


# ---------------------------------------------------------------- tray

class TrayApp:
    def __init__(self, qapp: QApplication) -> None:
        self.qapp = qapp
        self.logo = QPixmap(str(LOGO))
        self.logo_icon = QIcon(self.logo)
        self.icons = {
            "idle": self.logo_icon,
            "running": badge_icon(self.logo, "#16a34a"),
            "failed": badge_icon(self.logo, "#dc2626"),
        }

        self.log_path: Path | None = None
        self.log_file = None
        self.state = RunState()
        self.running: bool | None = None
        self.failed = False

        self.window = MainWindow(self)

        self.tray = QSystemTrayIcon(self.logo_icon)
        menu = QMenu()
        for text, slot in (("Show", self.show_window), ("Run now", self.run_now),
                           ("Stop run", self.stop_run)):
            act = QAction(text, menu)
            act.triggered.connect(slot)
            menu.addAction(act)
        menu.addSeparator()
        site = QAction("Open bostonsalsa.org", menu)
        site.triggered.connect(lambda: QDesktopServices.openUrl(QUrl(SITE_URL)))
        menu.addAction(site)
        menu.addSeparator()
        quit_act = QAction("Quit tray app", menu)
        quit_act.triggered.connect(qapp.quit)
        menu.addAction(quit_act)
        self.menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._activated)
        self.tray.messageClicked.connect(self.show_window)
        self.tray.show()

        self.reload_log()
        self.timer = QTimer()
        self.timer.timeout.connect(self.poll)
        self.timer.start(2000)
        self.poll()

    def _activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            if self.window.isVisible():
                self.window.hide()
            else:
                self.show_window()

    def show_window(self) -> None:
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def run_now(self) -> None:
        if self.running:
            self.show_window()
            return
        r = systemctl("start", "--no-block", SERVICE)
        if r.returncode != 0:
            QMessageBox.warning(self.window, "Boston Salsa", r.stderr or "Could not start the review.")
        QTimer.singleShot(1500, self.poll)

    def stop_run(self) -> None:
        if not self.running:
            return
        if QMessageBox.question(self.window, "Boston Salsa",
                                "Stop the review in progress? Work already pushed stays pushed; "
                                "uncommitted changes stay in the working tree.") \
                == QMessageBox.Yes:
            systemctl("stop", "--no-block", SERVICE)

    # -- log tailing

    def _latest_log(self) -> Path | None:
        logs = run_logs()
        return logs[0] if logs else None

    def reload_log(self) -> None:
        if self.log_file:
            self.log_file.close()
            self.log_file = None
        self.log_path = self._latest_log()
        self.state = RunState()
        if self.window.following():  # leave an older run the user picked on screen
            self.window.activity.clear()
        if self.log_path is None:
            if self.window.following():
                self.window.activity.setHtml("<i>No runs yet. Click “Run now” or wait for the schedule.</i>")
            return
        self.log_file = open(self.log_path, encoding="utf-8", errors="replace")
        self._read_new()

    def _read_new(self) -> None:
        if not self.log_file:
            return
        chunk = []
        while True:
            pos = self.log_file.tell()
            line = self.log_file.readline()
            if not line:
                break
            if not line.endswith("\n"):  # partial line: wait for the rest
                self.log_file.seek(pos)
                break
            rendered = self.state.render(line)
            if rendered:
                chunk.append(rendered)
        self.window.append_activity("".join(chunk))

    # -- polling

    def poll(self) -> None:
        latest = self._latest_log()
        if latest != self.log_path:
            self.reload_log()
            self.window.refresh_runs()
        else:
            self._read_new()

        svc = unit_props(SERVICE, "ActiveState", "Result")
        running = svc.get("ActiveState") in ("active", "activating", "deactivating")
        next_run = unit_props(TIMER, "NextElapseUSecRealtime").get("NextElapseUSecRealtime", "")
        enabled = systemctl("is-enabled", TIMER).stdout.strip() == "enabled"

        if self.running is None and not running:  # first poll: show the last run's outcome
            self.failed = svc.get("Result") not in ("success", "")
        if self.running is not None and running != self.running:
            if running:
                self.failed = False
                self.tray.showMessage("Boston Salsa", "Weekly review started — click to watch.",
                                      self.icons["running"], 8000)
            else:
                self.failed = svc.get("Result") not in ("success", "")
                self.window.load_summary()
                msg = ("Review failed — click to see what happened." if self.failed
                       else "Review finished — click for the summary.")
                if self._summary_flags():
                    msg += " ⚠ A scraper needs attention."
                self.tray.showMessage("Boston Salsa", msg,
                                      self.icons["failed" if self.failed else "idle"], 15000)
                self.window.tabs.setCurrentIndex(0 if self.failed else 1)
        self.running = running

        icon = "running" if running else ("failed" if self.failed else "idle")
        self.tray.setIcon(self.icons[icon])

        if running:
            detail = self.state.status_line() or "starting"
            status = f"<b>Running</b> — {html.escape(detail)}"
            if self.state.last_action:
                status += f'<br><span style="color:gray">{html.escape(self.state.last_action)}</span>'
            tip = f"Boston Salsa review: {detail}"
        else:
            when = next_run if (enabled and next_run and next_run != "n/a") else "not scheduled"
            status = f"<b>Idle</b> — next run: {html.escape(when)}"
            if self.failed:
                status += '<br><span style="color:#dc2626">Last run failed.</span>'
            tip = f"Boston Salsa — next review: {when}"
        if self.state.mcp_problem:
            status += f'<br><span style="color:#dc2626">{html.escape(self.state.mcp_problem)}</span>'
        self.window.status.setText(status)
        self.window.next_run.setText(next_run if enabled and next_run else "not scheduled")
        self.window.run_btn.setEnabled(not running)
        self.window.stop_btn.setEnabled(running)
        self.tray.setToolTip(tip)

    def _summary_flags(self) -> bool:
        try:
            return "NEEDS REDESIGN" in SUMMARY.read_text(encoding="utf-8")
        except OSError:
            return False


def main() -> int:
    QApplication.setDesktopFileName(APP_ID)
    qapp = QApplication(sys.argv)
    qapp.setApplicationName("Boston Salsa")
    qapp.setQuitOnLastWindowClosed(False)

    # One instance: a second launch (e.g. from the app menu) shows the window.
    sock = QLocalSocket()
    sock.connectToServer(APP_ID)
    if sock.waitForConnected(300):
        sock.write(b"show")
        sock.flush()
        sock.waitForBytesWritten(300)
        return 0
    QLocalServer.removeServer(APP_ID)
    server = QLocalServer()
    server.listen(APP_ID)

    if not QSystemTrayIcon.isSystemTrayAvailable():
        print("No system tray available.", file=sys.stderr)

    app = TrayApp(qapp)
    server.newConnection.connect(lambda: (server.nextPendingConnection(), app.show_window()))
    if "--show" in sys.argv:
        app.show_window()
    return qapp.exec()


if __name__ == "__main__":
    sys.exit(main())
