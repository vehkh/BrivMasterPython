"""Port of Monitor/IC_BrivMaster_Monitor.ahk - a small always-on-top-ish
window watching Logs/MiniLog.json (written when 'Output mini log' is on).

Run with:  python -m brivmaster.monitor [--minilog PATH]

Columns and alerting match the AHK original: BPH | Total | Active | Wait |
Cycle | Fail, with the last-update age going amber/red past thresholds and
the taskbar flashing on red. Settings persist in
IC_BrivMaster_Monitor_Settings.json next to this file.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from PySide6.QtCore import QTimer
from PySide6.QtGui import Qt
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QSpinBox,
                               QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget, QDialog, QFormLayout,
                               QDialogButtonBox, QCheckBox)

SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "IC_BrivMaster_Monitor_Settings.json")
DEFAULTS = {"Rows": 6, "Freq": 2000, "Amber": 40, "Red": 60, "Dark": False}


def load_settings():
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            settings = {**DEFAULTS, **json.load(f)}
    except (OSError, ValueError):
        settings = dict(DEFAULTS)
        save_settings(settings)
    return settings


def save_settings(settings):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=1)
    except OSError:
        pass


def default_minilog_path():
    package_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates = [os.path.join(package_root, "Logs", "MiniLog.json"),
                  os.path.join(os.path.dirname(package_root), "IC_BrivMaster",
                               "Logs", "MiniLog.json"),
                  os.path.join(os.path.dirname(package_root), "BrivMaster",
                               "..", "Logs", "MiniLog.json")]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return candidates[0]


class MonitorWindow(QWidget):
    def __init__(self, minilog_path):
        super().__init__()
        self.setWindowTitle("Briv Master Monitor")
        self.minilog_path = minilog_path
        self.settings = load_settings()
        self.last_mtime = 0
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("Time since last update (s):"))
        self.age_label = QLabel("-")
        top.addWidget(self.age_label)
        top.addStretch(1)
        settings_button = QPushButton("⚙")
        settings_button.setFixedWidth(28)
        settings_button.clicked.connect(self._open_settings)
        top.addWidget(settings_button)
        layout.addLayout(top)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["BPH", "Total", "Active", "Wait", "Cycle", "Fail"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        for column, width in enumerate((60, 55, 55, 55, 45, 40)):
            self.table.setColumnWidth(column, width)
        layout.addWidget(self.table)
        if self.settings["Dark"]:
            self.setStyleSheet("background-color: #303030; color: white;")
        self._alert_state = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(int(self.settings["Freq"]))
        self.resize(330, 60 + 22 * int(self.settings["Rows"]))
        self._tick()

    def _open_settings(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Settings")
        form = QFormLayout(dialog)
        spins = {}
        for key, label, lo, hi in (("Rows", "Runs to display", 1, 99),
                                   ("Freq", "Update frequency (ms)", 100, 999999),
                                   ("Amber", "Amber alert threshold (s)", 1, 999),
                                   ("Red", "Red alert threshold (s)", 2, 999)):
            spin = QSpinBox()
            spin.setRange(lo, hi)
            spin.setValue(int(self.settings[key]))
            form.addRow(label, spin)
            spins[key] = spin
        dark = QCheckBox()
        dark.setChecked(bool(self.settings["Dark"]))
        form.addRow("Dark mode", dark)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec():
            for key, spin in spins.items():
                self.settings[key] = spin.value()
            if self.settings["Red"] <= self.settings["Amber"]:
                self.settings["Red"] = self.settings["Amber"] + 1
            self.settings["Dark"] = dark.isChecked()
            save_settings(self.settings)
            self.timer.setInterval(int(self.settings["Freq"]))
            self.setStyleSheet("background-color: #303030; color: white;"
                               if self.settings["Dark"] else "")

    def _tick(self):
        try:
            mtime = os.path.getmtime(self.minilog_path)
        except OSError:
            self.age_label.setText("no log")
            return
        if mtime != self.last_mtime:
            self.last_mtime = mtime
            self._add_run()
        import time
        age = int(time.time() - mtime)
        self.age_label.setText(str(age))
        # Alert colouring + taskbar flash (FlashWindowEx in the original;
        # QApplication.alert is the portable equivalent)
        state = 2 if age > self.settings["Red"] \
            else 1 if age > self.settings["Amber"] else 0
        if state != self._alert_state:
            colour = {2: "red", 1: "#FFA000",
                      0: "white" if self.settings["Dark"] else "black"}[state]
            self.age_label.setStyleSheet(f"color: {colour};")
            if state == 2:
                QApplication.alert(self, 0)
            self._alert_state = state

    def _add_run(self):
        try:
            with open(self.minilog_path, "r", encoding="utf-8") as f:
                content = f.read()
            if not content:
                return  # file read between creation and population
            run = json.loads(content)
        except (OSError, ValueError):
            return
        duration = run.get("End", 0) - run.get("Start", 0)
        total = round(duration / 1000, 2)
        if run.get("ActiveStart"):
            load_time = run["ActiveStart"] - run["Start"]
            reset_time = run["End"] - run.get("ResetReached", run["End"])
            wait = round((load_time + reset_time) / 1000, 2)
            active = round((run.get("ResetReached", run["End"])
                            - run["ActiveStart"]) / 1000, 2)
            bosses = run.get("LastZone", 0) // 5
            bph = round(bosses * (3600000 / duration), 2) if duration else "-"
        else:  # incomplete run, possibly the first
            wait = active = "-"
            bph = "Partial"
        fail = "Fail" if run.get("Fail") else "-"
        self.table.insertRow(0)
        for column, value in enumerate((bph, total, active, wait,
                                        run.get("Cycle", ""), fail)):
            item = QTableWidgetItem(str(value))
            item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(0, column, item)
        while self.table.rowCount() > int(self.settings["Rows"]):
            self.table.removeRow(self.table.rowCount() - 1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--minilog", default=default_minilog_path())
    args = parser.parse_args()
    if not os.path.isfile(args.minilog):
        print(f"Unable to find MiniLog to monitor: {args.minilog}")
        return 1
    app = QApplication(sys.argv)
    window = MonitorWindow(args.minilog)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
