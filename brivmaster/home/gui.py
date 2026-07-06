"""Briv Master Home - PySide6 GUI with editable settings (AHK GUI parity).

Tabs mirror the AHK original: Briv Master (live control + tools), BM Game
(game location, log options, offsets, chest/daily settings), BM Route
(route grid editor + import/export, jumps, stacking zones, offline settings,
Casino), BM Levels (level manager + Feat Guard). 'Save Settings' writes ALL
tabs to IC_BrivMaster_Settings.json and pings a running farm to re-read it
(most settings still apply at farm start, as in the AHK original).

Not ported (niche/cosmetic): the AHK theme colour table (a Dark-mode toggle
replaces it) and the in-game settings profiles (use the AHK Home for those).
"""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QGridLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton,
    QSpinBox, QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..farm.shared_data import default_settings_path
from ..ipc import IpcError
from ..run_farm import default_offsets_path
from . import offsets_tool
from .hub import EllywickDealer, HomeHub
from .settings_io import (merge_with_template, route_export_string,
                          route_import_string, save_settings)

JUMP_COLOUR, STACK_COLOUR, OFF_COLOUR = "#3fae4a", "#c94f4f", "#e0e0e0"
CARD_TYPES = ((1, "Knight"), (2, "Moon"), (3, "Gem"), (4, "Fates"),
              (5, "Flames"))


class Binder:
    """Binds widgets to settings keys; load() from and apply() to the dict."""

    def __init__(self, settings):
        self.settings = settings
        self._bound = []  # (path tuple, widget, kind)

    def _container(self, path):
        container = self.settings
        for key in path[:-1]:
            container = container.setdefault(key, {})
        return container

    def add(self, path, widget, kind):
        self._bound.append((tuple(path), widget, kind))
        return widget

    def bool_(self, path, label):
        box = QCheckBox(label)
        return self.add(path, box, "bool")

    def int_(self, path, minimum=0, maximum=10_000_000):
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        return self.add(path, spin, "int")

    def text(self, path):
        return self.add(path, QLineEdit(), "text")

    def load(self):
        for path, widget, kind in self._bound:
            value = self._container(path).get(path[-1])
            if kind == "bool":
                widget.setChecked(bool(value))
            elif kind == "int":
                try:
                    widget.setValue(int(value or 0))
                except (TypeError, ValueError):
                    widget.setValue(0)
            elif kind == "text":
                widget.setText("" if value is None else str(value))
            elif kind == "choice":
                index = widget.findData(value)
                widget.setCurrentIndex(max(index, 0))

    def apply(self):
        for path, widget, kind in self._bound:
            container = self._container(path)
            if kind == "bool":
                container[path[-1]] = 1 if widget.isChecked() else 0
            elif kind == "int":
                container[path[-1]] = widget.value()
            elif kind == "text":
                container[path[-1]] = widget.text()
            elif kind == "choice":
                container[path[-1]] = widget.currentData()


def _form(group_title, rows):
    """A QGroupBox with label/widget rows."""
    group = QGroupBox(group_title)
    grid = QGridLayout(group)
    row_index = 0
    for entry in rows:
        if isinstance(entry, tuple):
            label, widget = entry
            grid.addWidget(QLabel(label), row_index, 0)
            grid.addWidget(widget, row_index, 1)
        else:  # bare widget (checkbox spans both columns)
            grid.addWidget(entry, row_index, 0, 1, 2)
        row_index += 1
    return group


class HomeWindow(QMainWindow):
    def __init__(self, hub):
        super().__init__()
        self.hub = hub
        self.binder = Binder(hub.ctx.settings)
        self.setWindowTitle("Briv Master Home")
        tabs = QTabWidget()
        tabs.addTab(self._build_main_tab(), "Briv Master")
        tabs.addTab(self._build_game_tab(), "BM Game")
        tabs.addTab(self._build_route_tab(), "BM Route")
        tabs.addTab(self._build_levels_tab(), "BM Levels")
        self.setCentralWidget(tabs)
        self.binder.load()
        self._load_route_grid()
        self._load_levels_table()
        self._load_elly_cards()
        self._apply_dark_mode()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(500)
        self._reconnect_countdown = 0

    # ================= Briv Master tab =================

    def _build_main_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        buttons = QHBoxLayout()
        for text, slot in (("Launch Game", self.hub.Launch_Clicked),
                           ("Start Farm", self._start_farm),
                           ("Stop Farm", self.hub.Stop_Clicked),
                           ("Reconnect", self.hub.Connect_Clicked),
                           ("Save Settings", self._save_settings),
                           ("Reset Stats", self._reset_stats)):
            button = QPushButton(text)
            button.clicked.connect(slot)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        run_group = QGroupBox("Run Control")
        run_grid = QGridLayout(run_group)
        self.pause_button = QPushButton("Pause offline")
        self.pause_button.setCheckable(True)
        self.pause_button.toggled.connect(
            lambda checked: self.hub.shared_set(
                "IBM_RunControl_DisableOffline", bool(checked)))
        self.queue_button = QPushButton("Queue offline")
        self.queue_button.clicked.connect(
            lambda: self.hub.shared_set("IBM_RunControl_ForceOffline", True))
        run_grid.addWidget(self.pause_button, 0, 0)
        run_grid.addWidget(self.queue_button, 0, 1)
        self.cycle_label = QLabel("-")
        self.strategy_label = QLabel("-")
        self.strategy_label.setWordWrap(True)
        self.stacking_label = QLabel("-")
        self.stacking_label.setWordWrap(True)
        self.stage_label = QLabel("-")
        self.last_close_label = QLabel("-")
        for row, (title, label) in enumerate((
                ("Cycle:", self.cycle_label),
                ("Strategy:", self.strategy_label),
                ("Stacking:", self.stacking_label),
                ("Stage:", self.stage_label),
                ("Last Close:", self.last_close_label)), start=1):
            run_grid.addWidget(QLabel(title), row, 0, Qt.AlignTop)
            run_grid.addWidget(label, row, 1)
        layout.addWidget(run_group)

        stats_group = QGroupBox("Run Stats")
        stats_layout = QVBoxLayout(stats_group)
        self.stats_label = QLabel("-")
        self.stats_label.setWordWrap(True)
        self.stats_runs_label = QLabel("-")
        self.stats_runs_label.setWordWrap(True)
        stats_layout.addWidget(self.stats_runs_label)
        stats_layout.addWidget(self.stats_label)
        layout.addWidget(stats_group)

        chest_group = QGroupBox("Chests && Daily Platinum")
        chest_layout = QVBoxLayout(chest_group)
        self.chest_summary = QLabel("-")
        chest_layout.addWidget(self.chest_summary)
        self.chest_log = QPlainTextEdit()
        self.chest_log.setReadOnly(True)
        self.chest_log.setMaximumHeight(110)
        chest_layout.addWidget(self.chest_log)
        layout.addWidget(chest_group)

        game_settings_group = QGroupBox("Game Settings")
        gs_grid = QGridLayout(game_settings_group)
        self.gs_profile_combo = QComboBox()
        self._reload_gs_profile_combo()
        self.gs_profile_combo.currentIndexChanged.connect(self._gs_profile_changed)
        gs_check = QPushButton("Check now")
        gs_check.clicked.connect(lambda: self._gs_run(False))
        gs_set = QPushButton("Set Now")
        gs_set.clicked.connect(lambda: self._gs_run(True))
        gs_edit = QPushButton("⚙ Profiles")
        gs_edit.clicked.connect(self._gs_edit_profiles)
        self.gs_status = QLabel("(first check ~60s after start)")
        self.gs_status.setWordWrap(True)
        gs_grid.addWidget(QLabel("Profile:"), 0, 0)
        gs_grid.addWidget(self.gs_profile_combo, 0, 1)
        gs_grid.addWidget(gs_check, 0, 2)
        gs_grid.addWidget(gs_set, 0, 3)
        gs_grid.addWidget(gs_edit, 0, 4)
        gs_grid.addWidget(self.gs_status, 1, 0, 1, 5)
        layout.addWidget(game_settings_group)

        elly_group = QGroupBox("Ellywick Non-Gemfarm Re-roll Tool")
        elly_grid = QGridLayout(elly_group)
        self.elly_spins = {}
        for column, (card_type, name) in enumerate(CARD_TYPES):
            elly_grid.addWidget(QLabel(name), 0, column * 2, 1, 2,
                                Qt.AlignCenter)
            spin_min = QSpinBox(); spin_min.setRange(0, 5)
            spin_max = QSpinBox(); spin_max.setRange(0, 5)
            elly_grid.addWidget(spin_min, 1, column * 2)
            elly_grid.addWidget(spin_max, 1, column * 2 + 1)
            self.elly_spins[card_type] = (spin_min, spin_max)
        elly_start = QPushButton("Start")
        elly_start.clicked.connect(self._elly_start)
        elly_stop = QPushButton("Stop")
        elly_stop.clicked.connect(self._elly_stop)
        self.elly_status = QLabel("-")
        elly_grid.addWidget(elly_start, 2, 0, 1, 2)
        elly_grid.addWidget(elly_stop, 2, 2, 1, 2)
        elly_grid.addWidget(self.elly_status, 2, 4, 1, 6)
        layout.addWidget(elly_group)
        layout.addStretch(1)
        return page

    # ================= BM Game tab =================

    def _build_game_tab(self):
        binder = self.binder
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(_form("Game Location", [
            ("Executable:", binder.text(["IBM_Game_Exe"])),
            ("Location:", binder.text(["IBM_Game_Path"])),
            ("Launch command:", binder.text(["IBM_Game_Launch"])),
            binder.bool_(["IBM_Game_Hide_Launcher"], "Hide launcher"),
        ]))
        layout.addWidget(_form("Log Options", [
            binder.bool_(["IBM_Logger_MiniLog"], "Output mini log"),
            binder.bool_(["IBM_Logger_ZoneLog"], "Log zone progression"),
            binder.bool_(["IBM_Theme_Current", "DarkMode"], "Dark mode"),
        ]))
        layout.addWidget(_form("Chests && Daily Platinum settings (HUB)", [
            ("Gold to buy per call:",
             binder.int_(["HUB", "IBM_ChestSnatcher_Options_Min_Buy"], 0, 250)),
            ("Gold to open per call:",
             binder.int_(["HUB", "IBM_ChestSnatcher_Options_Open_Gold"], 0, 1000)),
            ("Silver to open per call:",
             binder.int_(["HUB", "IBM_ChestSnatcher_Options_Open_Silver"], 0, 1000)),
            ("Reserve Gems:",
             binder.int_(["HUB", "IBM_ChestSnatcher_Options_Min_Gem"])),
            ("Reserve Gold:",
             binder.int_(["HUB", "IBM_ChestSnatcher_Options_Min_Gold"])),
            ("Reserve Silver:",
             binder.int_(["HUB", "IBM_ChestSnatcher_Options_Min_Silver"])),
            binder.bool_(["HUB", "IBM_DailyRewardClaim_Enable"],
                         "Claim Daily Rewards"),
        ]))

        offsets_group = QGroupBox("Offsets")
        offsets_grid = QGridLayout(offsets_group)
        versions = self.hub.ctx.memory.Versions
        self.offsets_current = QLabel(
            f"Current: imports {self.hub.ctx.memory.GetImportsVersion()}, "
            f"pointers {versions.get('Pointer_Version_Major')}"
            f"{versions.get('Pointer_Version_Minor')} "
            f"{versions.get('Pointer_Revision')} "
            f"({offsets_tool.platform_name(versions.get('Platform'))})")
        self.offsets_github = QLabel("GitHub: (not checked)")
        self.offsets_status = QLabel("")
        self.offsets_status.setWordWrap(True)
        check_button = QPushButton("Check now")
        check_button.clicked.connect(self._offsets_check)
        download_button = QPushButton("Download")
        download_button.clicked.connect(self._offsets_download)
        self.offsets_lock = binder.bool_(["HUB", "IBM_Offsets_Lock_Pointers"],
                                         "Imports only (preserve pointers)")
        offsets_grid.addWidget(self.offsets_current, 0, 0, 1, 2)
        offsets_grid.addWidget(self.offsets_github, 1, 0, 1, 2)
        offsets_grid.addWidget(check_button, 2, 0)
        offsets_grid.addWidget(download_button, 2, 1)
        offsets_grid.addWidget(self.offsets_lock, 3, 0, 1, 2)
        offsets_grid.addWidget(self.offsets_status, 4, 0, 1, 2)
        layout.addWidget(offsets_group)
        layout.addStretch(1)
        return page

    # ================= BM Route tab =================

    def _build_route_tab(self):
        binder = self.binder
        page = QWidget()
        layout = QVBoxLayout(page)

        layout.addWidget(_form("Starting Strategy", [
            binder.bool_(["IBM_Route_Combine"], "Combine Thellora and Briv"),
            binder.bool_(["IBM_Route_Combine_Boss_Avoidance"], "Avoid Bosses"),
        ]))

        route_group = QGroupBox("Route (click to toggle; top row = jump on Q, "
                                "bottom = online stack allowed)")
        route_layout = QVBoxLayout(route_group)
        self.route_table = QTableWidget(2, 50)
        self.route_table.setVerticalHeaderLabels(["Jump", "Stack"])
        self.route_table.setHorizontalHeaderLabels(
            [str(i + 1) for i in range(50)])
        for column in range(50):
            self.route_table.setColumnWidth(column, 18)
        self.route_table.setMaximumHeight(110)
        self.route_table.cellClicked.connect(self._route_cell_toggle)
        route_layout.addWidget(self.route_table)
        import_row = QHBoxLayout()
        self.route_string_edit = QLineEdit()
        self.route_string_edit.setPlaceholderText(
            "{jump,stack} string from Emmote's routes site")
        import_button = QPushButton("Import")
        import_button.clicked.connect(self._route_import)
        export_button = QPushButton("Export")
        export_button.clicked.connect(self._route_export)
        import_row.addWidget(self.route_string_edit)
        import_row.addWidget(import_button)
        import_row.addWidget(export_button)
        route_layout.addLayout(import_row)
        layout.addWidget(route_group)

        two_columns = QHBoxLayout()
        two_columns.addWidget(_form("Briv Jumps", [
            ("Q:", binder.int_(["IBM_Route_BrivJump_Q"], 0, 400)),
            ("E:", binder.int_(["IBM_Route_BrivJump_E"], 0, 400)),
            ("M:", binder.int_(["IBM_Route_BrivJump_M"], 0, 400)),
        ]))
        two_columns.addWidget(_form("Stacking Zones", [
            ("Offline:", binder.int_(["IBM_Offline_Stack_Zone"], 0, 2500)),
            ("Min recovery:", binder.int_(["IBM_Offline_Stack_Min"], 0, 2500)),
            ("Target online:", binder.int_(["IBM_Online_Melf_Min"], 0, 2500)),
            ("Farideh trigger:", self._farideh_condition_combo()),
            ("Farideh threshold:",
             binder.int_(["IBM_Online_Farideh_Threshold"], 0, 2000)),
        ]))
        layout.addLayout(two_columns)

        two_columns2 = QHBoxLayout()
        two_columns2.addWidget(_form("Offline Settings", [
            ("Platform login (ms):",
             binder.int_(["IBM_OffLine_Delay_Time"], 0, 15000)),
            ("Restart sleep (ms):",
             binder.int_(["IBM_OffLine_Sleep_Time"], 0, 60000)),
            ("Timeout factor:", binder.int_(["IBM_OffLine_Timeout"], 1, 20)),
            ("Offline every x runs:", binder.int_(["IBM_OffLine_Freq"], 1, 10000)),
            binder.bool_(["IBM_Route_Offline_Restore_Window"], "Restore window"),
            binder.bool_(["IBM_OffLine_Blank"], "Blank restarts"),
            binder.bool_(["IBM_OffLine_Blank_Stop"], "Stop progress"),
            binder.bool_(["IBM_OffLine_Blank_Relay"], "Relay restarts"),
            ("Relay start zone:",
             binder.int_(["IBM_OffLine_Blank_Relay_Zones"], 0, 2500)),
        ]))
        two_columns2.addWidget(_form("Ellywick's Casino", [
            ("Target Gem cards:", binder.int_(["IBM_Casino_Target_Base"], 0, 5)),
            ("Maximum redraws:", binder.int_(["IBM_Casino_Redraws_Base"], 0, 2)),
            ("Minimum cards:", binder.int_(["IBM_Casino_MinCards_Base"], 0, 5)),
        ]))
        layout.addLayout(two_columns2)
        layout.addStretch(1)
        return page

    def _farideh_condition_combo(self):
        combo = QComboBox()
        for value, label in ((1, "Active enemies"), (2, "Attacking enemies"),
                             (3, "Tatyana return (ms)")):
            combo.addItem(label, value)
        self.binder.add(["IBM_Online_Farideh_Condition"], combo, "choice")
        return combo

    # ================= BM Levels tab =================

    def _build_levels_tab(self):
        binder = self.binder
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(_form("Levelling Options", [
            ("Max sequential keys:",
             binder.int_(["IBM_LevelManager_Input_Max"], 2, 12)),
            ("Modifier key:", self._modifier_key_combo()),
            ("Modifier value:", self._modifier_value_combo()),
            binder.bool_(["IBM_LevelManager_Boost_Use"], "Briv Level Boost"),
            ("Safety Factor:",
             binder.int_(["IBM_LevelManager_Boost_Multi"], 1, 100)),
            binder.bool_(["IBM_Level_Diana_Cheese"], "Dynamic Diana"),
            binder.bool_(["IBM_Level_Recovery_Softcap"], "Recovery Levelling"),
            binder.bool_(["IBM_Level_Options_Suppress_Front"],
                         "Suppress Front Row"),
            binder.bool_(["IBM_Level_Options_Ghost"], "Ghost Level"),
        ]))
        manager_group = QGroupBox("Level Manager")
        manager_layout = QVBoxLayout(manager_group)
        button_row = QHBoxLayout()
        for text, slot in (("Refresh Formations", self._levels_refresh),
                           ("Add champion", self._levels_add_row),
                           ("Remove selected", self._levels_remove_row),
                           ("Feat Guard: save current", self._feat_guard_save),
                           ("Feat Guard: clear", self._feat_guard_clear)):
            button = QPushButton(text)
            button.clicked.connect(slot)
            button_row.addWidget(button)
        manager_layout.addLayout(button_row)
        self.levels_table = QTableWidget(0, 7)
        self.levels_table.setHorizontalHeaderLabels(
            ["Hero ID", "Name", "Start (z1)", "Priority", "Prio limit",
             "Normal (min)", "Feats"])
        manager_layout.addWidget(self.levels_table)
        self.levels_note = QLabel(
            "Feats column: count of Feat Guard feats, '+' = non-exclusive. "
            "Levels save with Save Settings.")
        self.levels_note.setWordWrap(True)
        manager_layout.addWidget(self.levels_note)
        layout.addWidget(manager_group)
        return page

    def _modifier_key_combo(self):
        combo = QComboBox()
        for value in ("Ctrl", "Shift"):
            combo.addItem(value, value)
        self.binder.add(["IBM_Level_Options_Mod_Key"], combo, "choice")
        return combo

    def _modifier_value_combo(self):
        combo = QComboBox()
        for value in (10, 25):
            combo.addItem(f"x{value}", value)
        self.binder.add(["IBM_Level_Options_Mod_Value"], combo, "choice")
        return combo

    # ---- route grid helpers ----

    def _load_route_grid(self):
        settings = self.hub.ctx.settings
        jump = settings.get("IBM_Route_Zones_Jump", []) or []
        stack = settings.get("IBM_Route_Zones_Stack", []) or []
        for column in range(50):
            for row, data, colour in ((0, jump, JUMP_COLOUR),
                                      (1, stack, STACK_COLOUR)):
                value = data[column] if column < len(data) else 0
                item = QTableWidgetItem("")
                item.setFlags(Qt.ItemIsEnabled)
                item.setData(Qt.UserRole, 1 if value else 0)
                item.setBackground(QColor(colour if value else OFF_COLOUR))
                self.route_table.setItem(row, column, item)

    def _route_cell_toggle(self, row, column):
        item = self.route_table.item(row, column)
        new_value = 0 if item.data(Qt.UserRole) else 1
        item.setData(Qt.UserRole, new_value)
        colour = (JUMP_COLOUR if row == 0 else STACK_COLOUR) if new_value \
            else OFF_COLOUR
        item.setBackground(QColor(colour))

    def _route_grid_arrays(self):
        jump, stack = [], []
        for column in range(50):
            jump.append(self.route_table.item(0, column).data(Qt.UserRole) or 0)
            stack.append(self.route_table.item(1, column).data(Qt.UserRole) or 0)
        return jump, stack

    def _route_import(self):
        changed = route_import_string(self.hub.ctx.settings,
                                      self.route_string_edit.text().strip())
        if any(changed):
            self._load_route_grid()
            self.status_label.setText(
                f"Route import: jump={'updated' if changed[0] else 'kept'}, "
                f"stack={'updated' if changed[1] else 'kept'} (Save to keep)")
        else:
            self.status_label.setText("Route import: no valid {jump,stack} string")

    def _route_export(self):
        jump, stack = self._route_grid_arrays()
        settings = dict(self.hub.ctx.settings)
        settings["IBM_Route_Zones_Jump"] = jump
        settings["IBM_Route_Zones_Stack"] = stack
        text = route_export_string(settings)
        self.route_string_edit.setText(text)
        QApplication.clipboard().setText(text)
        self.status_label.setText("Route string exported to box + clipboard")

    # ---- levels table helpers ----

    def _load_levels_table(self):
        levels = self.hub.ctx.settings.get("IBM_LevelManager_Levels", {}) or {}
        self.levels_table.setRowCount(0)
        for hero_id, data in self._levels_sorted(levels):
            self._levels_append_row(hero_id, data)

    def _levels_sorted(self, levels):
        """Rows ordered by seat then hero ID, as the AHK GUI lists them;
        falls back to plain ID order when the game is not readable."""
        def sort_key(pair):
            hero_id = int(pair[0])
            seat = self._hero_seat(hero_id)
            return (seat if seat else 99, hero_id)
        return sorted(levels.items(), key=sort_key)

    def _hero_seat(self, hero_id):
        """Champion seat from game memory; None when not readable."""
        try:
            memory = self.hub.ctx.memory
            if not memory.IsAttached:
                exe = self.hub.ctx.setting("IBM_Game_Exe", "IdleDragons.exe")
                if memory.AttachToReadyInstance(exe, wait_s=0) is None:
                    return None
            hero = self.hub.ctx.heroes[int(hero_id)]
            return hero.ReadChampSeat() if hero else None
        except Exception:  # noqa: BLE001 - ordering is cosmetic
            return None

    def _hero_name(self, hero_id):
        """Champion name from game memory (as the AHK GUI's ReadName);
        empty string when the game is not readable."""
        try:
            hero_id = int(hero_id)
        except (TypeError, ValueError):
            return ""
        try:
            memory = self.hub.ctx.memory
            if not memory.IsAttached:
                exe = self.hub.ctx.setting("IBM_Game_Exe", "IdleDragons.exe")
                if memory.AttachToReadyInstance(exe, wait_s=0) is None:
                    return ""
            hero = self.hub.ctx.heroes[hero_id]
            return (hero.ReadName() or "") if hero else ""
        except Exception:  # noqa: BLE001 - names are cosmetic
            return ""

    def _levels_append_row(self, hero_id, data):
        row = self.levels_table.rowCount()
        self.levels_table.insertRow(row)
        feat_list = data.get("Feat_List") or {}
        feats = (f"{len(feat_list)}"
                 f"{'' if data.get('Feat_Exclusive') else '+'}")
        values = (hero_id, self._hero_name(hero_id), data.get("z1", 0),
                  data.get("prio", 0), data.get("priolimit", ""),
                  data.get("min", 0), feats)
        for column, value in enumerate(values):
            item = QTableWidgetItem("" if value in (None, "") else str(value))
            if column in (1, 6):  # Name and Feats are display-only
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.levels_table.setItem(row, column, item)

    def _levels_collect(self):
        """Read the table back into the IBM_LevelManager_Levels shape,
        preserving Feat_* data for unchanged champions."""
        old = self.hub.ctx.settings.get("IBM_LevelManager_Levels", {}) or {}
        levels = {}
        for row in range(self.levels_table.rowCount()):
            def cell(column):
                item = self.levels_table.item(row, column)
                return item.text().strip() if item else ""
            hero_id = cell(0)
            if not hero_id.isdigit():
                continue
            previous = old.get(hero_id) or old.get(int(hero_id)) or {}
            def num(column, default=0):
                text = cell(column)
                return int(text) if text.lstrip("-").isdigit() else default
            levels[hero_id] = {
                "z1": num(2), "prio": num(3),
                "priolimit": num(4) if cell(4) else "",
                "min": num(5),
                "Feat_List": previous.get("Feat_List", ""),
                "Feat_Exclusive": previous.get("Feat_Exclusive", ""),
            }
        return levels

    def _levels_refresh(self):
        """Refresh Formations: union of saved Q/W/E/M champions; add missing
        rows with zeroed levels."""
        memory = self.hub.ctx.memory
        exe = self.hub.ctx.setting("IBM_Game_Exe", "IdleDragons.exe")
        if memory.AttachToReadyInstance(exe, wait_s=0) is None:
            self.status_label.setText("Refresh Formations: game not readable")
            return
        merged = self._levels_collect()  # keep unsaved edits + Feat data
        added = 0
        slots = [memory.GetSavedFormationSlotByFavorite(1),
                 memory.GetSavedFormationSlotByFavorite(2),
                 memory.GetSavedFormationSlotByFavorite(3),
                 memory.GetActiveModronFormationSaveSlot()]
        for slot in slots:
            if slot is None or slot < 0:
                continue
            for champ in (memory.GetFormationSaveBySlot(slot, True) or []):
                if champ and str(champ) not in merged:
                    merged[str(champ)] = {"z1": 0, "prio": 0,
                                          "priolimit": "", "min": 0}
                    added += 1
        # Rebuild seat-ordered; also (re)fills names now the game is readable
        self.levels_table.setRowCount(0)
        for hero_id, data in self._levels_sorted(merged):
            self._levels_append_row(hero_id, data)
        self.status_label.setText(f"Refresh Formations: added {added} champion(s)")

    def _levels_add_row(self):
        self._levels_append_row("", {"z1": 0, "prio": 0, "priolimit": "",
                                     "min": 0})

    def _levels_remove_row(self):
        row = self.levels_table.currentRow()
        if row >= 0:
            self.levels_table.removeRow(row)

    def _selected_hero_id(self):
        row = self.levels_table.currentRow()
        if row < 0:
            return None, None
        item = self.levels_table.item(row, 0)
        return (int(item.text()), row) if item and item.text().isdigit() \
            else (None, None)

    def _feat_guard_save(self):
        hero_id, row = self._selected_hero_id()
        if hero_id is None:
            self.status_label.setText("Feat Guard: select a champion row first")
            return
        memory = self.hub.ctx.memory
        exe = self.hub.ctx.setting("IBM_Game_Exe", "IdleDragons.exe")
        if memory.AttachToReadyInstance(exe, wait_s=0) is None:
            self.status_label.setText("Feat Guard: game not readable")
            return
        slots = memory.GameManager.game.gameInstances[0].Controller \
            .userData.FeatHandler.heroFeatSlots.dict_value(hero_id)
        feats = {}
        if slots is not None:
            feat_list = slots.child("List")
            size = feat_list.size() or 0
            for index in range(min(size, 10)):
                feat_id = feat_list[index].ID.read()
                if feat_id:
                    feats[str(feat_id)] = feat_list[index].Name.read() or ""
        exclusive = QMessageBox.question(
            self, "Feat Guard",
            f"Save {len(feats)} equipped feat(s) for hero {hero_id}.\n\n"
            "Use EXCLUSIVE mode (equipped feats must match exactly; "
            "otherwise extra feats are allowed)?",
            QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes
        levels = self.hub.ctx.settings.setdefault("IBM_LevelManager_Levels", {})
        entry = levels.setdefault(str(hero_id), {"z1": 0, "prio": 0,
                                                 "priolimit": "", "min": 0})
        entry["Feat_List"] = feats
        entry["Feat_Exclusive"] = 1 if exclusive else ""
        self.levels_table.item(row, 5).setText(
            f"{len(feats)}{'' if exclusive else '+'}")
        self.status_label.setText(f"Feat Guard saved for hero {hero_id} "
                                  "(Save Settings to persist)")

    def _feat_guard_clear(self):
        hero_id, row = self._selected_hero_id()
        if hero_id is None:
            return
        levels = self.hub.ctx.settings.get("IBM_LevelManager_Levels", {})
        entry = levels.get(str(hero_id)) or levels.get(hero_id)
        if entry:
            entry["Feat_List"] = ""
            entry["Feat_Exclusive"] = ""
        self.levels_table.item(row, 5).setText("0+")
        self.status_label.setText(f"Feat Guard cleared for hero {hero_id}")

    # ---- Elly cards <-> HUB setting ----

    def _load_elly_cards(self):
        cards = self.hub.ctx.settings.get("HUB", {}) \
            .get("IBM_Ellywick_NonGemFarm_Cards") or [0] * 10
        for index, (card_type, _name) in enumerate(CARD_TYPES):
            spin_min, spin_max = self.elly_spins[card_type]
            if len(cards) > index * 2 + 1:
                spin_min.setValue(int(cards[index * 2] or 0))
                spin_max.setValue(int(cards[index * 2 + 1] or 0))

    def _apply_elly_cards(self):
        cards = []
        for card_type, _name in CARD_TYPES:
            spin_min, spin_max = self.elly_spins[card_type]
            cards.extend([spin_min.value(), spin_max.value()])
        self.hub.ctx.settings.setdefault("HUB", {})[
            "IBM_Ellywick_NonGemFarm_Cards"] = cards

    # ---- actions ----

    def _save_settings(self):
        settings = self.hub.ctx.settings
        self.binder.apply()
        jump, stack = self._route_grid_arrays()
        settings["IBM_Route_Zones_Jump"] = jump
        settings["IBM_Route_Zones_Stack"] = stack
        levels = self._levels_collect()
        # As in AHK: ignore level settings if no champions are shown at all
        if levels:
            settings["IBM_LevelManager_Levels"] = levels
        self._apply_elly_cards()
        merge_with_template(settings)
        try:
            save_settings(settings, self.hub.settings_path)
        except OSError as err:
            self.status_label.setText(f"Save FAILED: {err}")
            return
        applied = False
        if self.hub.farm_ipc is not None:
            try:
                self.hub.farm_ipc.call("shared", "UpdateSettingsFromFile")
                applied = True
            except IpcError:
                pass
        self._apply_dark_mode()
        self.status_label.setText(
            "Settings saved" + (" (farm re-read them; most apply at next "
                                "farm start)" if applied else ""))

    def _apply_dark_mode(self):
        dark = bool(self.hub.ctx.settings.get("IBM_Theme_Current", {})
                    .get("DarkMode"))
        self.setStyleSheet(
            "QMainWindow, QWidget { background-color: #333333; color: #C0C0C0; }"
            "QLineEdit, QSpinBox, QComboBox, QPlainTextEdit, QTableWidget "
            "{ background-color: #555555; color: #E0E0E0; }"
            "QPushButton { background-color: #4a4a4a; color: #E0E0E0; "
            "padding: 3px 8px; }"
            "QGroupBox { border: 1px solid #666; margin-top: 6px; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 6px; }"
            if dark else "")

    def _reset_stats(self):
        self.hub.stats.reset()
        if self.hub.farm_ipc is not None:
            try:
                self.hub.farm_ipc.call("shared", "ResetRunStats")
            except IpcError:
                pass
        self.status_label.setText("Stats reset")

    def _start_farm(self):
        if not self.hub.IsGameOpen():
            QMessageBox.warning(self, "Briv Master",
                                "The game does not appear to be running.")
            return
        self.hub.Run_Clicked()
        self._reconnect_countdown = 20

    def _offsets_check(self):
        result = offsets_tool.check_versions(self.hub.ctx.memory,
                                             self.hub.ctx.settings)
        if result.get("error"):
            self.offsets_status.setText(result["error"])
        github = (f"GitHub: imports {result.get('github_imports') or '?'}, "
                  f"pointers {result.get('github_pointers') or '?'} "
                  f"(game {result.get('game') or '?'})")
        self.offsets_github.setText(github)

    def _offsets_download(self):
        import os
        offsets_dir = os.path.dirname(os.path.abspath(
            default_offsets_path()))
        confirm = QMessageBox.question(
            self, "Briv Master",
            f"Download offsets into:\n{offsets_dir}\n\n"
            f"{'Pointers preserved (imports only).' if self.offsets_lock.isChecked() else 'Pointers AND imports will be replaced.'}"
            "\nProceed?", QMessageBox.Yes | QMessageBox.No)
        if confirm != QMessageBox.Yes:
            return
        status = offsets_tool.download_offsets(
            self.hub.ctx.memory, self.hub.ctx.settings, offsets_dir,
            lock_pointers=self.offsets_lock.isChecked())
        self.offsets_status.setText(status)

    # ---- game settings profiles ----

    def _reload_gs_profile_combo(self):
        hub_settings = self.hub.ctx.settings.get("HUB", {})
        profiles = hub_settings.get("IBM_Game_Settings_Option_Set") or []
        selected = int(hub_settings.get("IBM_Game_Settings_Option_Profile", 1) or 1)
        self.gs_profile_combo.blockSignals(True)
        self.gs_profile_combo.clear()
        for index, profile in enumerate(profiles, start=1):
            self.gs_profile_combo.addItem(
                profile.get("Name") or f"Profile {index}", index)
        combo_index = self.gs_profile_combo.findData(selected)
        self.gs_profile_combo.setCurrentIndex(max(combo_index, 0))
        self.gs_profile_combo.blockSignals(False)

    def _gs_profile_changed(self):
        value = self.gs_profile_combo.currentData()
        if value:
            self.hub.ctx.settings.setdefault("HUB", {})[
                "IBM_Game_Settings_Option_Profile"] = value
            self.status_label.setText(
                f"Game settings profile {value} selected (Save Settings to keep)")

    def _gs_run(self, change):
        if change and not self.hub.GameSettings.is_game_closed():
            QMessageBox.warning(
                self, "Briv Master",
                "Game settings cannot be changed whilst Idle Champions "
                "is running")
            return
        self.hub.GameSettings.check(change)
        self._gs_show_status()

    def _gs_show_status(self):
        game_settings = self.hub.GameSettings
        if not game_settings.status:
            return
        colour = {"TrafficLightGood": "#3fae4a", "TrafficLightBad": "#e04040",
                  "TrafficLightNeutral": "#e0a020"}.get(
                      game_settings.status_level, "")
        self.gs_status.setStyleSheet(f"color: {colour};" if colour else "")
        self.gs_status.setText(game_settings.status)
        # Differences go in the tooltip, as in the AHK original
        self.gs_status.setToolTip(game_settings.detail)

    def _gs_edit_profiles(self):
        from PySide6.QtWidgets import (QDialog, QDialogButtonBox)
        hub_settings = self.hub.ctx.settings.setdefault("HUB", {})
        profiles = hub_settings.setdefault("IBM_Game_Settings_Option_Set", [])
        while len(profiles) < 2:
            profiles.append({"Name": f"Profile {len(profiles) + 1}"})
        fields = [("Name", "Name", "text"),
                  ("Framerate", "Framerate", "int"),
                  ("Particles", "% Particles", "int"),
                  ("HRes", "H. Resolution", "int"),
                  ("VRes", "V. Resolution", "int"),
                  ("Fullscreen", "Fullscreen", "bool"),
                  ("CapFPSinBG", "Cap FPS in BG", "bool"),
                  ("SaveFeats", "Save Feats", "bool"),
                  ("ConsolePortraits", "Console Portraits", "bool"),
                  ("NarrowHero", "Narrow Hero Boxes", "bool"),
                  ("AllHero", "Show All Heroes", "bool"),
                  ("Swap25100", "Swap x25 and x100", "bool")]
        dialog = QDialog(self)
        dialog.setWindowTitle("Game Settings Profiles")
        grid = QGridLayout(dialog)
        grid.addWidget(QLabel("<b>Profile 1</b>"), 0, 1)
        grid.addWidget(QLabel("<b>Profile 2</b>"), 0, 2)
        editors = []  # (key, kind, widget1, widget2)
        for row, (key, label, kind) in enumerate(fields, start=1):
            grid.addWidget(QLabel(label + ":"), row, 0)
            row_widgets = []
            for profile in profiles[:2]:
                value = profile.get(key)
                if kind == "bool":
                    widget = QCheckBox()
                    widget.setChecked(bool(value))
                elif kind == "int":
                    widget = QSpinBox()
                    widget.setRange(0, 100000)
                    try:
                        widget.setValue(int(value or 0))
                    except (TypeError, ValueError):
                        widget.setValue(0)
                else:
                    widget = QLineEdit(str(value or ""))
                row_widgets.append(widget)
            grid.addWidget(row_widgets[0], row, 1)
            grid.addWidget(row_widgets[1], row, 2)
            editors.append((key, kind, row_widgets[0], row_widgets[1]))
        note = QLabel("Level Amount is always x100 - not editable, by design.")
        grid.addWidget(note, len(fields) + 1, 0, 1, 3)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        grid.addWidget(buttons, len(fields) + 2, 0, 1, 3)
        if not dialog.exec():
            return
        for key, kind, widget1, widget2 in editors:
            for profile, widget in ((profiles[0], widget1),
                                    (profiles[1], widget2)):
                if kind == "bool":
                    profile[key] = 1 if widget.isChecked() else 0
                elif kind == "int":
                    profile[key] = widget.value()
                else:
                    profile[key] = widget.text()
        self._reload_gs_profile_combo()
        self.status_label.setText("Profiles updated (Save Settings to keep)")

    def _elly_start(self):
        min_cards, max_cards = {}, {}
        for card_type, _name in CARD_TYPES:
            spin_min, spin_max = self.elly_spins[card_type]
            min_cards[card_type] = spin_min.value()
            max_cards[card_type] = spin_max.value()
        exe = self.hub.ctx.setting("IBM_Game_Exe", "IdleDragons.exe")
        if self.hub.ctx.memory.AttachToReadyInstance(exe, wait_s=0) is None:
            self.elly_status.setText("Game not readable")
            return
        self.hub.EllyDealer = EllywickDealer(self.hub, min_cards, max_cards)
        self.hub.EllyDealer.Start()

    def _elly_stop(self):
        if self.hub.EllyDealer is not None:
            self.hub.EllyDealer.Stop()
            self.elly_status.setText("Stopped")

    # ---- timer ----

    def _tick(self):
        hub = self.hub
        if self._reconnect_countdown > 0 and hub.farm_ipc is None:
            self._reconnect_countdown -= 1
            hub.Connect_Clicked()
        snapshot = hub.Update()
        connected = hub.farm_ipc is not None
        status = hub.status_message or ("Connected" if connected
                                        else "Farm not connected")
        self.status_label.setText(status)
        self.cycle_label.setText(str(
            snapshot.get("IBM_RunControl_CycleString", "-")))
        self.strategy_label.setText(str(
            snapshot.get("IBM_RunControl_StatusString", "-")))
        self.stacking_label.setText(str(
            snapshot.get("IBM_RunControl_StackString", "-")))
        self.stage_label.setText(str(snapshot.get("LoopString", "-")))
        self.last_close_label.setText(str(snapshot.get("LastCloseReason", "-")))
        self.stats_runs_label.setText(hub.stats.summary())
        self.stats_label.setText(
            f"Bosses hit (run/total): {snapshot.get('BossesHitThisRun', '-')}/"
            f"{snapshot.get('TotalBossesHit', '-')}   "
            f"Rollbacks: {snapshot.get('TotalRollBacks', '-')}   "
            f"Bad autoprogress: {snapshot.get('BadAutoProgress', '-')}")
        self.chest_summary.setText(
            f"Gems: {hub.CurrentGems:,}   "
            f"Silver: {hub.Chests['CurrentSilver']:,} "
            f"(+{hub.Chests['PurchasedSilver']:,}/-{hub.Chests['OpenedSilver']:,})   "
            f"Gold: {hub.Chests['CurrentGold']:,} "
            f"(+{hub.Chests['PurchasedGold']:,}/-{hub.Chests['OpenedGold']:,})")
        messages = hub.ChestSnatcher.Messages[-8:]
        self.chest_log.setPlainText("\n".join(
            f"[{m['Time']}] {m['Action']}: {m['Comment']}" for m in messages))
        self._gs_show_status()  # reflect the hourly background check
        if hub.EllyDealer is not None:
            if hub.EllyDealer.running:
                hub.EllyDealer.Tick()
            self.elly_status.setText(hub.EllyDealer.status)


def main():
    settings = default_settings_path()
    offsets = default_offsets_path()
    app = QApplication(sys.argv)
    hub = HomeHub(settings, offsets)
    window = HomeWindow(hub)
    window.resize(620, 820)
    window.show()
    hub.Connect_Clicked()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
