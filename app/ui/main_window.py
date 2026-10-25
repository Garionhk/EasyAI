"""The application window: four tabs, a status bar, and the startup sequence.

Startup deliberately does the slow, failure-prone part behind a splash so the
first thing a beginner sees is "Starting the AI engine…" rather than a frozen
window or a console.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QProgressBar,
    QPushButton, QTabWidget, QVBoxLayout, QWidget,
)

from app.comfy.client import ComfyClient
from app.comfy.launcher import ComfyLauncher
from app import VERSION_LABEL, i18n
from app.config import Config, ensure_folders
from app.i18n import plural, t
from app.jobs import EngineWorker
from app.queue import QueueManager
from app.modes import MODE_ORDER, MODES
from app.ui import theme
from app.ui.manifest_editor import ManifestEditor
from app.ui.queue_tab import QueueTab
from app.ui.read_tab import ReadTab
from app.ui.settings_dialog import SettingsDialog
from app.ui.tabs import build_tab
from app.ui.widgets import open_folder


class StartupSplash(QDialog):
    """Shown while ComfyUI is being found or started."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("EasyAI")
        self.setModal(True)
        self.setFixedSize(440, 190)
        self.setWindowFlag(Qt.WindowCloseButtonHint, False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)

        title = QLabel("EasyAI")
        title.setObjectName("Big")
        layout.addWidget(title)

        self.message = QLabel(t("Starting up…"))
        self.message.setWordWrap(True)
        layout.addWidget(self.message)

        bar = QProgressBar()
        bar.setRange(0, 0)          # indeterminate
        layout.addWidget(bar)
        layout.addStretch(1)

        row = QHBoxLayout()
        row.addStretch(1)
        self.skip_btn = QPushButton(t("Carry on without it"))
        self.skip_btn.setToolTip(t(
            "Open EasyAI anyway. You will not be able to create anything "
            "until ComfyUI is running."))
        row.addWidget(self.skip_btn)
        layout.addLayout(row)

    def set_message(self, text: str) -> None:
        self.message.setText(text)


class MainWindow(QMainWindow):
    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self.client = ComfyClient(cfg.server)
        self.launcher = ComfyLauncher(
            cfg.get("comfyui_dir"), cfg.get("comfyui_launcher"),
            self.client, timeout=int(cfg.get("launch_timeout") or 300),
        )
        self.engine_worker: EngineWorker | None = None
        #: One queue for all three tabs - there is one graphics card, so work
        #: runs in turn rather than at once.
        self.queue = QueueManager(self.client, self)
        self.queue.emptied.connect(self._on_queue_empty)
        #: Set once closing is really going ahead, so the guard does not ask
        #: again on the way out.
        self._closing_for_good = False

        self.setWindowTitle(f"{t('EasyAI — simple AI creation')}  ·  {VERSION_LABEL}")
        self.resize(1360, 880)
        self.setMinimumSize(1040, 700)

        self._build_tabs()
        self._build_menu()
        self._build_status_bar()

        geometry = cfg.get("window_geometry")
        if geometry:
            try:
                self.restoreGeometry(bytes.fromhex(geometry))
            except ValueError:
                pass

    # ------------------------------------------------------------------ UI
    def _build_tabs(self) -> None:
        shell = QWidget()
        column = QVBoxLayout(shell)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)

        # A live readout of the engine, next to the tabs rather than buried in
        # the status bar - it is the first thing to check when nothing works.
        strip = QHBoxLayout()
        strip.setContentsMargins(14, 8, 14, 0)
        self.engine_pill = QLabel(t("engine · checking…"))
        self.engine_pill.setObjectName("EnginePill")
        strip.addWidget(self.engine_pill)
        strip.addStretch(1)
        self.workflow_count = QLabel("")
        self.workflow_count.setObjectName("Counter")
        strip.addWidget(self.workflow_count)
        column.addLayout(strip)

        self.tabs = QTabWidget()
        self.tab_by_mode = {}
        for key in MODE_ORDER:
            mode = MODES[key]
            tab = build_tab(key, self.cfg, self.client, self)
            tab.status_message.connect(self._set_status)
            tab.edit_requested.connect(self._open_manifest_editor)
            if hasattr(tab, "send_to_requested"):
                tab.send_to_requested.connect(self._send_prompt_to)
            self.tabs.addTab(tab, f"{mode.icon}  {t(mode.label)}")
            self.tab_by_mode[key] = tab
            tab.attach_queue(self.queue)

        # The queue is not a Mode, so it is added directly rather than through
        # MODE_ORDER. It sits last because it is where you look afterwards.
        self.queue_tab = QueueTab(self.queue, self.cfg)
        self.tabs.addTab(self.queue_tab, "📋  " + t("Queue"))

        # Nor is reading a prompt back. It needs no engine at all, which is
        # why it sits apart from the three that do.
        self.read_tab = ReadTab(self.cfg)
        self.read_tab.send_to_requested.connect(self._send_prompt_to)
        self.read_tab.send_read_requested.connect(self._send_read_to)
        self.read_tab.status_message.connect(self._set_status)
        self.tabs.addTab(self.read_tab, "🔎  " + t("Read a prompt"))

        column.addWidget(self.tabs, 1)
        self.setCentralWidget(shell)

        # Ctrl+Enter runs whatever tab is in front.
        run = QShortcut(QKeySequence("Ctrl+Return"), self)
        run.activated.connect(self._run_current_tab)
        QShortcut(QKeySequence("Ctrl+Enter"), self).activated.connect(
            self._run_current_tab)

    def _run_current_tab(self) -> None:
        tab = self.tabs.currentWidget()
        if tab is not None and tab.create_btn.isEnabled() and tab.create_btn.isVisible():
            tab._on_create()

    def _build_menu(self) -> None:
        bar = self.menuBar()

        file_menu = bar.addMenu(t("&File"))
        self._add_action(file_menu, t("Open the workflows folder"), self._open_workflow_folder)
        self._add_action(file_menu, t("Open the results folder"), self._open_output_folder)
        file_menu.addSeparator()
        self._add_action(file_menu, t("Settings…"), self._open_settings, QKeySequence("Ctrl+,"))
        file_menu.addSeparator()
        self._add_action(file_menu, t("Quit"), self.close, QKeySequence.Quit)

        engine_menu = bar.addMenu(t("&AI engine"))
        self._add_action(engine_menu, t("Check it is running"), self._check_engine, QKeySequence("F5"))
        self._add_action(engine_menu, t("Start it now"), self._start_engine)
        self._add_action(engine_menu, t("Reload workflows"), self._reload_all, QKeySequence("Ctrl+R"))

        help_menu = bar.addMenu(t("&Help"))
        self._add_action(help_menu, t("How do I add a workflow?"), self._show_help)
        self._add_action(help_menu, t("About EasyAI"), self._show_about)

    def _add_action(self, menu, text, slot, shortcut=None) -> None:
        action = QAction(text, self)
        action.triggered.connect(slot)
        if shortcut:
            action.setShortcut(shortcut)
        menu.addAction(action)

    def _build_status_bar(self) -> None:
        self.vram_label = QLabel("")
        self.vram_label.setObjectName("Mono")
        self.engine_dot = QLabel("●")
        self.engine_dot.setStyleSheet(f"color:{theme.BAD};font-size:13px;")
        self.engine_text = QLabel(t("AI engine: not connected"))
        self.statusBar().addPermanentWidget(self.vram_label)
        self.statusBar().addPermanentWidget(self.engine_text)
        self.statusBar().addPermanentWidget(self.engine_dot)
        self._set_status(t("Ready"))

    def _set_status(self, message: str) -> None:
        self.statusBar().showMessage(message, 8000)

    def _set_engine_indicator(self, alive: bool) -> None:
        self.engine_dot.setStyleSheet(
            f"color:{theme.OK if alive else theme.BAD};font-size:13px;")
        self.engine_text.setText(
            t("AI engine: running") if alive else t("AI engine: not running"))
        self.engine_pill.setText(
            t("engine live · {server}", server=self.cfg.server) if alive
            else t("engine down · {server}", server=self.cfg.server))
        self.engine_pill.setStyleSheet(
            f"color:{theme.MUTED if alive else theme.BAD};")
        self._refresh_vram(alive)

    def _refresh_vram(self, alive: bool) -> None:
        """Show the card's memory, so a failed video run has an explanation."""
        if not alive:
            self.vram_label.setText("")
            return
        try:
            devices = self.client.system_stats().get("devices") or []
        except Exception:
            self.vram_label.setText("")
            return
        for device in devices:
            total = device.get("vram_total") or 0
            free = device.get("vram_free") or 0
            if total:
                used = (total - free) / (1024 ** 3)
                self.vram_label.setText(t(
                    "VRAM {used} / {total} GB",
                    used=f"{used:.1f}", total=f"{total / 1024 ** 3:.0f}"))
                return
        self.vram_label.setText("")

    def _update_workflow_count(self) -> None:
        total = sum(len(tab.workflows) for tab in self.tab_by_mode.values())
        self.workflow_count.setText(
            plural(total, "{n} workflow loaded", "{n} workflows loaded"))

    def retranslate(self) -> None:
        """Switch the whole window to the newly chosen language, in place."""
        self.setWindowTitle(f"{t('EasyAI — simple AI creation')}  ·  {VERSION_LABEL}")

        self.menuBar().clear()
        self._build_menu()

        for index, key in enumerate(MODE_ORDER):
            self.tabs.setTabText(index, f"{MODES[key].icon}  {t(MODES[key].label)}")
        for tab in self.tab_by_mode.values():
            tab.retranslate()

        # The two tabs that are not Modes have to be named here, or they keep
        # the language they were built in.
        self.tabs.setTabText(len(MODE_ORDER), "📋  " + t("Queue"))
        self.tabs.setTabText(len(MODE_ORDER) + 1,
                             "🔎  " + t("Read a prompt"))
        self.queue_tab.retranslate()
        self.read_tab.retranslate()

        self._set_engine_indicator(self.client.is_alive())
        self._update_workflow_count()
        self._set_status(t("Ready"))

    # ------------------------------------------------------------- startup
    def start(self) -> None:
        """Bring the engine up behind a splash, then load the workflows."""
        ensure_folders(self.cfg)

        if self.client.is_alive():
            self._after_engine(True, t("Already running."))
            return

        self.splash = StartupSplash(self)
        self.splash.skip_btn.clicked.connect(self._skip_startup)

        self.engine_worker = EngineWorker(
            self.launcher, bool(self.cfg.get("auto_launch")), self)
        self.engine_worker.status.connect(self.splash.set_message)
        self.engine_worker.done.connect(self._on_engine_ready)
        self.engine_worker.start()
        self.splash.exec()

    def _skip_startup(self) -> None:
        if self.engine_worker:
            self.engine_worker.cancel()
        self.splash.accept()
        self._after_engine(False, "")

    def _on_engine_ready(self, ok: bool, message: str) -> None:
        if hasattr(self, "splash") and self.splash.isVisible():
            self.splash.accept()
        self._after_engine(ok, message)

    def _after_engine(self, ok: bool, message: str) -> None:
        self._set_engine_indicator(ok)
        for tab in self.tab_by_mode.values():
            tab.invalidate_capabilities()
        self._reload_all()

        if not ok and message:
            QMessageBox.warning(
                self, t("The AI engine did not start"),
                message + "\n\n" + t("You can still look around EasyAI, but you "
                                     "will not be able to create anything yet."))
        elif ok:
            self._set_status(t("AI engine is ready."))

    # ------------------------------------------------------------- actions
    def _reload_all(self) -> None:
        for tab in self.tab_by_mode.values():
            tab.reload(check=True)
        self._update_workflow_count()

    def _check_engine(self) -> None:
        alive = self.client.is_alive()
        self._set_engine_indicator(alive)
        self._set_status(t("AI engine is running.") if alive
                         else t("AI engine is not responding."))
        if alive:
            for tab in self.tab_by_mode.values():
                tab.invalidate_capabilities()
            self._reload_all()

    def _start_engine(self) -> None:
        if self.client.is_alive():
            QMessageBox.information(self, t("Already running"),
                                    t("The AI engine is already running."))
            return
        self.launcher.comfyui_dir = Path(self.cfg.get("comfyui_dir"))
        self.launcher.launcher_name = self.cfg.get("comfyui_launcher")
        self.start()

    def _send_prompt_to(self, mode_key: str, text: str) -> None:
        """Drop an improved prompt into another tab and switch to it."""
        tab = self.tab_by_mode.get(mode_key)
        if not tab:
            return
        tab.prompt_box.setPlainText(text)
        self.tabs.setCurrentWidget(tab)
        tab.prompt_box.setFocus()
        self._set_status(t("Prompt sent to {tab}.", tab=t(MODES[mode_key].label)))

    def _send_read_to(self, mode_key: str, found) -> None:
        """A prompt read back from a picture, into the workflow that made it."""
        tab = self.tab_by_mode.get(mode_key)
        if not tab:
            return
        picked = tab.use_read_prompt(found)
        self.tabs.setCurrentWidget(tab)
        tab.prompt_box.setFocus()
        if picked:
            self._set_status(t("Prompt sent to {tab}, with {name} chosen.",
                               tab=t(MODES[mode_key].label), name=picked))
        else:
            self._set_status(t("Prompt sent to {tab}.", tab=t(MODES[mode_key].label)))

    def _open_manifest_editor(self, workflow) -> None:
        editor = ManifestEditor(workflow, self)
        if editor.exec() == QDialog.Accepted:
            self._reload_all()
            self._set_status(t("Saved the setup for {name}.", name=workflow.name))

    def _open_settings(self) -> None:
        before = (self.cfg.get("comfyui_dir"), self.cfg.get("comfyui_launcher"),
                  self.cfg.server, str(self.cfg.workflow_dir()))
        language_before = i18n.current()
        dialog = SettingsDialog(self.cfg, self.client, self)
        if dialog.exec() != QDialog.Accepted:
            return

        if i18n.current() != language_before:
            self.retranslate()

        self.cfg.save()
        after = (self.cfg.get("comfyui_dir"), self.cfg.get("comfyui_launcher"),
                 self.cfg.server, str(self.cfg.workflow_dir()))
        if before != after:
            self.client.server = self.cfg.server
            self.client.clear_cache()
            replacement = ComfyLauncher(
                self.cfg.get("comfyui_dir"), self.cfg.get("comfyui_launcher"),
                self.client, timeout=int(self.cfg.get("launch_timeout") or 300))
            # Hand over any ComfyUI we started, or the new launcher would have
            # no way to close it and it would outlive EasyAI.
            replacement.adopt(self.launcher)
            self.launcher = replacement
            ensure_folders(self.cfg)
            QTimer.singleShot(0, self._check_engine)
        self._reload_all()

    def _open_workflow_folder(self) -> None:
        self._reveal(self.cfg.workflow_dir())

    def _open_output_folder(self) -> None:
        self._reveal(self.cfg.output_dir())

    def _reveal(self, folder: Path) -> None:
        open_folder(folder)

    def _show_help(self) -> None:
        QMessageBox.information(
            self, t("Adding a workflow"),
            t("1. Open the workflow in ComfyUI and check it runs.\n\n"
              "2. Choose  Workflow → Export (API).\n"
              "    A plain Save will not work — EasyAI needs the API version.\n\n"
              "3. Save the file into the folder for its type, inside:\n"
              "    {folder}\n\n"
              "4. Back in EasyAI, press Refresh.\n\n"
              "EasyAI works out which parts of the workflow to control. If it "
              "guesses wrong, press 'Set up…' to correct it.\n\n"
              "Tip: put a picture next to the file with the same name "
              "(flux.json → flux.png) and it becomes the thumbnail.",
              folder=self.cfg.workflow_dir()))

    def _show_about(self) -> None:
        QMessageBox.about(
            self, t("About EasyAI"),
            f"<b>EasyAI {VERSION_LABEL}</b><br><br>"
            + t("A simple front end for ComfyUI.<br><br>"
                "Pick a style, type what you want, choose a shape, press "
                "Create.<br><br>"
                "Workflows: {workflows}<br>Results: {results}",
                workflows=self.cfg.workflow_dir(),
                results=self.cfg.output_dir()))

    # ------------------------------------------------------------ shutdown
    def _on_queue_empty(self) -> None:
        """Close when the last item finishes, if that was asked for.

        The point of a long queue: set up an evening's work, tick the box and
        walk away. Stopping ComfyUI too is the existing setting's job.

        It counts down in the open rather than closing at once, because the
        person who ticked this may still be sitting there - a single quick job
        finishing should not make the window vanish with no way to stop it.
        """
        if not self.cfg.get("close_when_queue_empty"):
            return
        if self._closing_for_good:
            return
        self._start_closing_countdown()

    #: Long enough to read the message and reach the button, short enough that
    #: someone who has walked away is not kept waiting.
    CLOSE_COUNTDOWN = 20

    def _start_closing_countdown(self) -> None:
        left = self.CLOSE_COUNTDOWN
        box = QMessageBox(self)
        box.setWindowTitle(t("The queue is empty"))
        box.setIcon(QMessageBox.Information)
        box.setText(t("Everything in the queue is finished."))
        stay = box.addButton(t("Stay open"), QMessageBox.RejectRole)
        now = box.addButton(t("Close now"), QMessageBox.AcceptRole)
        box.setDefaultButton(stay)

        def tick():
            nonlocal left
            box.setInformativeText(plural(
                left, "EasyAI will close in {n} second, as you asked.",
                "EasyAI will close in {n} seconds, as you asked."))
            if left <= 0:
                box.accept()
            left -= 1

        timer = QTimer(self)
        timer.timeout.connect(tick)
        tick()
        timer.start(1000)
        box.exec()
        timer.stop()

        if box.clickedButton() is stay:
            # Staying means the choice is withdrawn, or the next job to finish
            # would ask all over again.
            self.cfg.set("close_when_queue_empty", False)
            if hasattr(self, "queue_tab"):
                self.queue_tab.close_when_done.setChecked(False)
            self._set_status(t("Staying open. The switch has been turned off."))
            return

        del now
        self._closing_for_good = True
        self.close()

    def closeEvent(self, event) -> None:
        # Work that is still queued would be lost, and a viewer who has left a
        # queue running should not lose it to a stray click on the X.
        outstanding = self.queue.unfinished() if self.queue else []
        if outstanding and not self._closing_for_good:
            running = sum(1 for i in outstanding if i is self.queue.running)
            waiting = len(outstanding) - running
            box = QMessageBox(self)
            box.setWindowTitle(t("Still working"))
            box.setIcon(QMessageBox.Warning)
            box.setText(plural(
                len(outstanding),
                "{n} item is still in the queue.",
                "{n} items are still in the queue."))
            box.setInformativeText(t(
                "{running} running, {waiting} waiting. Closing now would throw "
                "that work away.\n\n"
                "To be told when it is done, tick “Close EasyAI when the "
                "queue is empty” on the Queue tab.",
                running=running, waiting=waiting))
            keep = box.addButton(t("Keep working"), QMessageBox.RejectRole)
            box.addButton(t("Cancel everything and close"),
                          QMessageBox.DestructiveRole)
            box.setDefaultButton(keep)
            box.exec()

            if box.clickedButton() is keep:
                event.ignore()
                self.tabs.setCurrentWidget(self.queue_tab)
                return
            self._closing_for_good = True

        if self.queue:
            self.queue.stop_everything()
        for tab in self.tab_by_mode.values():
            tab.stop_work()
        if self.engine_worker and self.engine_worker.isRunning():
            self.engine_worker.cancel()
            self.engine_worker.wait(3000)
        if self.cfg.get("stop_engine_on_exit"):
            self.launcher.stop()

        self.cfg.set("window_geometry", bytes(self.saveGeometry()).hex())
        self.cfg.save()
        event.accept()
