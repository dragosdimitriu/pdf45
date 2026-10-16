# SPDX-License-Identifier: GPL-3.0-or-later
"""Main window of the pdf/45 viewer.

Everything here is built on Qt 6's PDF module (QtPdf / QtPdfWidgets),
which embeds PDFium for rendering, text search and outline (bookmark)
extraction.  Printing goes through QtPrintSupport (see printing.py).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QModelIndex, QPointF, QSettings, QSize, Qt, QTimer, QUrl, Slot
from PySide6.QtGui import (
    QAction,
    QDragEnterEvent,
    QDropEvent,
    QIcon,
    QKeySequence,
)
from PySide6.QtPdf import QPdfBookmarkModel, QPdfDocument, QPdfLink, QPdfSearchModel
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDockWidget,
    QFileDialog,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QSpinBox,
    QStyle,
    QTabWidget,
    QToolBar,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from pdf45 import APP_ID, APP_NAME, ORG_NAME, __version__
from pdf45.credits import CREDITS_HTML, HELP_TEXT
from pdf45.printing import page_range_from_printer, print_document

MAX_RECENT = 10
ZOOM_STEP = 1.25
ZOOM_MIN = 0.1
ZOOM_MAX = 16.0


def _resource_path(name: str) -> Path:
    """Locate a bundled resource both in source checkouts and PyInstaller builds."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / "pdf45" / "resources" / name
    return Path(__file__).parent / "resources" / name


class SearchPanel(QWidget):
    """Search box plus a list of hits built from QPdfSearchModel."""

    def __init__(self, model: QPdfSearchModel, parent: QWidget | None = None):
        super().__init__(parent)
        self.model = model
        self.edit = QLineEdit(self)
        self.edit.setPlaceholderText("Search text… (Enter = next, Shift+Enter = previous)")
        self.edit.setClearButtonEnabled(True)
        self.count = QLabel("", self)
        self.results = QListWidget(self)
        self.results.setAlternatingRowColors(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.edit)
        layout.addWidget(self.count)
        layout.addWidget(self.results, 1)

        # Searching happens incrementally inside the model; rebuild the
        # visible list whenever it reports new rows.
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(60)
        self._refresh_timer.timeout.connect(self.rebuild)
        self.model.rowsInserted.connect(lambda *_: self._refresh_timer.start())
        self.model.rowsRemoved.connect(lambda *_: self._refresh_timer.start())
        self.model.modelReset.connect(lambda *_: self._refresh_timer.start())
        self.model.layoutChanged.connect(lambda *_: self._refresh_timer.start())

    def rebuild(self) -> None:
        self.results.clear()
        total = self.model.rowCount(QModelIndex())
        for i in range(total):
            link: QPdfLink = self.model.resultAtIndex(i)
            text = f"p.{link.page() + 1}: {link.contextBefore()}{self.model.searchString()}{link.contextAfter()}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, i)
            self.results.addItem(item)
        if self.model.searchString():
            self.count.setText(f"{total} result{'s' if total != 1 else ''}")
        else:
            self.count.setText("")


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1100, 800)
        self.setAcceptDrops(True)
        self.settings = QSettings(ORG_NAME, APP_ID)
        self.current_path: str | None = None

        icon_path = _resource_path("pdf45.png")
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        # --- document & models -------------------------------------------
        self.document = QPdfDocument(self)
        self.document.passwordRequired.connect(self._ask_password)
        self.document.statusChanged.connect(self._status_changed)

        self.search_model = QPdfSearchModel(self)
        self.search_model.setDocument(self.document)
        self.bookmark_model = QPdfBookmarkModel(self)
        self.bookmark_model.setDocument(self.document)

        # --- central view -------------------------------------------------
        self.view = QPdfView(self)
        self.view.setDocument(self.document)
        self.view.setPageMode(QPdfView.PageMode.MultiPage)
        self.view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        self.view.setSearchModel(self.search_model)
        self.setCentralWidget(self.view)
        self.navigator = self.view.pageNavigator()
        self.navigator.currentPageChanged.connect(self._page_changed)

        self._build_actions()
        self._build_toolbar()
        self._build_menus()
        self._build_sidebar()
        self.statusBar().showMessage("Open a PDF to begin (Ctrl+O).")
        self._restore_geometry()
        self._update_enabled()

    # ------------------------------------------------------------------ UI
    def _build_actions(self) -> None:
        style = self.style()

        def act(text, slot, shortcut=None, icon=None, checkable=False):
            a = QAction(text, self)
            if icon is not None:
                a.setIcon(style.standardIcon(icon))
            if isinstance(shortcut, list):
                a.setShortcuts(shortcut)
            elif isinstance(shortcut, QKeySequence.StandardKey):
                a.setShortcuts(QKeySequence.keyBindings(shortcut))
            elif shortcut is not None:
                a.setShortcut(shortcut)
            a.setCheckable(checkable)
            if slot is not None:
                a.triggered.connect(slot)
            return a

        SP = QStyle.StandardPixmap
        self.act_open = act("&Open…", self.open_dialog, QKeySequence.StandardKey.Open, SP.SP_DialogOpenButton)
        self.act_close = act("&Close", self.close_document, QKeySequence.StandardKey.Close)
        self.act_print = act("&Print…", self.print_dialog, QKeySequence.StandardKey.Print, SP.SP_FileDialogDetailedView)
        self.act_quit = act("&Quit", self.close, QKeySequence.StandardKey.Quit)

        self.act_prev = act("&Previous page", self.prev_page, [QKeySequence("PgUp"), QKeySequence("Left")], SP.SP_ArrowUp)
        self.act_next = act("&Next page", self.next_page, [QKeySequence("PgDown"), QKeySequence("Right")], SP.SP_ArrowDown)
        self.act_first = act("&First page", lambda: self.goto_page(0), QKeySequence("Home"))
        self.act_last = act("&Last page", lambda: self.goto_page(self.document.pageCount() - 1), QKeySequence("End"))
        self.act_goto = act("&Go to page…", self.goto_dialog, QKeySequence("Ctrl+G"))
        self.act_back = act("&Back", self.navigator.back, QKeySequence.StandardKey.Back, SP.SP_ArrowBack)
        self.act_forward = act("For&ward", self.navigator.forward, QKeySequence.StandardKey.Forward, SP.SP_ArrowForward)

        self.act_zoom_in = act("Zoom &in", self.zoom_in, [QKeySequence.StandardKey.ZoomIn, QKeySequence("Ctrl+=")])
        self.act_zoom_out = act("Zoom &out", self.zoom_out, QKeySequence.StandardKey.ZoomOut)
        self.act_zoom_100 = act("&Actual size", lambda: self.set_zoom(1.0), QKeySequence("Ctrl+0"))
        self.act_fit_width = act("Fit &width", lambda: self.view.setZoomMode(QPdfView.ZoomMode.FitToWidth), QKeySequence("Ctrl+2"))
        self.act_fit_page = act("Fit &page", lambda: self.view.setZoomMode(QPdfView.ZoomMode.FitInView), QKeySequence("Ctrl+1"))
        self.act_single = act("&Single page mode", self.toggle_page_mode, QKeySequence("Ctrl+Shift+S"), checkable=True)
        self.act_fullscreen = act("&Full screen", self.toggle_fullscreen, QKeySequence("F11"), checkable=True)
        self.act_sidebar = act("Show &sidebar", None, QKeySequence("F9"), checkable=True)

        self.act_find = act("&Find…", self.focus_search, QKeySequence.StandardKey.Find)
        self.act_find_next = act("Find &next", self.find_next, QKeySequence.StandardKey.FindNext)
        self.act_find_prev = act("Find &previous", self.find_prev, QKeySequence.StandardKey.FindPrevious)

        self.act_about = act("&About pdf/45", self.show_about)
        self.act_credits = act("&Credits", self.show_credits)
        self.act_help = act("&Help", self.show_help, QKeySequence.StandardKey.HelpContents)

    def _build_toolbar(self) -> None:
        tb = QToolBar("Main", self)
        tb.setObjectName("maintoolbar")
        tb.setMovable(False)
        tb.setIconSize(QSize(20, 20))
        self.addToolBar(tb)
        tb.addAction(self.act_open)
        tb.addAction(self.act_print)
        tb.addSeparator()
        tb.addAction(self.act_back)
        tb.addAction(self.act_forward)
        tb.addSeparator()
        tb.addAction(self.act_prev)

        self.page_spin = QSpinBox(self)
        self.page_spin.setMinimum(1)
        self.page_spin.setMaximum(1)
        self.page_spin.setKeyboardTracking(False)
        self.page_spin.valueChanged.connect(lambda v: self.goto_page(v - 1))
        tb.addWidget(self.page_spin)
        self.page_total = QLabel(" / 0 ", self)
        tb.addWidget(self.page_total)
        tb.addAction(self.act_next)
        tb.addSeparator()

        self.zoom_box = QComboBox(self)
        self.zoom_box.setEditable(True)
        self.zoom_box.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for label in ("Fit width", "Fit page", "50%", "75%", "100%", "125%", "150%", "200%", "300%", "400%"):
            self.zoom_box.addItem(label)
        self.zoom_box.setCurrentText("Fit width")
        self.zoom_box.activated.connect(self._zoom_box_changed)
        self.zoom_box.lineEdit().returnPressed.connect(lambda: self._zoom_box_changed(-1))
        tb.addWidget(self.zoom_box)
        tb.addAction(self.act_zoom_out)
        tb.addAction(self.act_zoom_in)
        self.view.zoomFactorChanged.connect(self._zoom_changed)
        self.view.zoomModeChanged.connect(lambda _m: self._zoom_changed(self.view.zoomFactor()))

    def _build_menus(self) -> None:
        mb = self.menuBar()
        m_file = mb.addMenu("&File")
        m_file.addAction(self.act_open)
        self.recent_menu = m_file.addMenu("Open &recent")
        m_file.addAction(self.act_close)
        m_file.addSeparator()
        m_file.addAction(self.act_print)
        m_file.addSeparator()
        m_file.addAction(self.act_quit)
        self._rebuild_recent_menu()

        m_edit = mb.addMenu("&Edit")
        m_edit.addAction(self.act_find)
        m_edit.addAction(self.act_find_next)
        m_edit.addAction(self.act_find_prev)

        m_view = mb.addMenu("&View")
        m_view.addAction(self.act_zoom_in)
        m_view.addAction(self.act_zoom_out)
        m_view.addAction(self.act_zoom_100)
        m_view.addAction(self.act_fit_width)
        m_view.addAction(self.act_fit_page)
        m_view.addSeparator()
        m_view.addAction(self.act_single)
        m_view.addAction(self.act_sidebar)
        m_view.addAction(self.act_fullscreen)

        m_go = mb.addMenu("&Go")
        for a in (self.act_first, self.act_prev, self.act_next, self.act_last, self.act_goto):
            m_go.addAction(a)
        m_go.addSeparator()
        m_go.addAction(self.act_back)
        m_go.addAction(self.act_forward)

        m_help = mb.addMenu("&Help")
        m_help.addAction(self.act_help)
        m_help.addSeparator()
        m_help.addAction(self.act_credits)
        m_help.addAction(self.act_about)

    def _build_sidebar(self) -> None:
        self.sidebar = QDockWidget("Sidebar", self)
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self.tabs = QTabWidget(self.sidebar)

        self.bookmarks = QTreeView(self.tabs)
        self.bookmarks.setModel(self.bookmark_model)
        self.bookmarks.setHeaderHidden(True)
        self.bookmarks.setExpandsOnDoubleClick(True)
        self.bookmarks.activated.connect(self._bookmark_activated)
        self.bookmarks.clicked.connect(self._bookmark_activated)
        self.tabs.addTab(self.bookmarks, "Bookmarks")

        self.search_panel = SearchPanel(self.search_model, self.tabs)
        self.search_panel.edit.textChanged.connect(self._search_text_changed)
        self.search_panel.edit.returnPressed.connect(self._search_return)
        self.search_panel.results.currentRowChanged.connect(self._search_result_selected)
        self.tabs.addTab(self.search_panel, "Search")

        self.sidebar.setWidget(self.tabs)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.sidebar)
        self.sidebar.setMinimumWidth(220)

        self.act_sidebar.setChecked(True)
        self.act_sidebar.toggled.connect(self.sidebar.setVisible)
        self.sidebar.visibilityChanged.connect(self.act_sidebar.setChecked)

    # -------------------------------------------------------------- state
    def _update_enabled(self) -> None:
        has_doc = self.document.status() == QPdfDocument.Status.Ready
        for a in (
            self.act_close, self.act_print, self.act_prev, self.act_next, self.act_first,
            self.act_last, self.act_goto, self.act_zoom_in, self.act_zoom_out, self.act_zoom_100,
            self.act_fit_width, self.act_fit_page, self.act_find, self.act_find_next, self.act_find_prev,
        ):
            a.setEnabled(has_doc)
        self.page_spin.setEnabled(has_doc)
        self.zoom_box.setEnabled(has_doc)
        self.act_back.setEnabled(self.navigator.backAvailable())
        self.act_forward.setEnabled(self.navigator.forwardAvailable())

    @Slot(QPdfDocument.Status)
    def _status_changed(self, status: QPdfDocument.Status) -> None:
        if status == QPdfDocument.Status.Ready:
            n = self.document.pageCount()
            self.page_spin.setMaximum(max(1, n))
            self.page_total.setText(f" / {n} ")
            title = self.document.metaData(QPdfDocument.MetaDataField.Title)
            name = os.path.basename(self.current_path or "")
            self.setWindowTitle(f"{title or name} — {APP_NAME}" if (title or name) else APP_NAME)
            self.bookmarks.expandToDepth(0)
            self.statusBar().showMessage(f"{name}: {n} page{'s' if n != 1 else ''}")
            self.navigator.jump(0, QPointF(), 0)
        elif status == QPdfDocument.Status.Error:
            self.statusBar().showMessage(f"Could not open: {self.document.error()}")
        elif status == QPdfDocument.Status.Null:
            self.page_spin.setMaximum(1)
            self.page_total.setText(" / 0 ")
            self.setWindowTitle(APP_NAME)
        self._update_enabled()

    @Slot(int)
    def _page_changed(self, page: int) -> None:
        self.page_spin.blockSignals(True)
        self.page_spin.setValue(page + 1)
        self.page_spin.blockSignals(False)
        self.act_back.setEnabled(self.navigator.backAvailable())
        self.act_forward.setEnabled(self.navigator.forwardAvailable())

    @Slot(float)
    def _zoom_changed(self, factor: float) -> None:
        mode = self.view.zoomMode()
        if mode == QPdfView.ZoomMode.FitToWidth:
            text = "Fit width"
        elif mode == QPdfView.ZoomMode.FitInView:
            text = "Fit page"
        else:
            text = f"{round(factor * 100)}%"
        self.zoom_box.blockSignals(True)
        self.zoom_box.setCurrentText(text)
        self.zoom_box.blockSignals(False)

    def _zoom_box_changed(self, _index: int) -> None:
        text = self.zoom_box.currentText().strip().lower()
        if text.startswith("fit w"):
            self.view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
            return
        if text.startswith("fit p"):
            self.view.setZoomMode(QPdfView.ZoomMode.FitInView)
            return
        try:
            value = float(text.rstrip("%").strip()) / 100.0
        except ValueError:
            self._zoom_changed(self.view.zoomFactor())
            return
        self.set_zoom(value)

    # ------------------------------------------------------------ actions
    @Slot()
    def open_dialog(self) -> None:
        start = os.path.dirname(self.current_path) if self.current_path else self.settings.value("lastDir", "")
        path, _ = QFileDialog.getOpenFileName(self, "Open PDF", start, "PDF documents (*.pdf);;All files (*)")
        if path:
            self.open_file(path)

    def open_file(self, path: str) -> bool:
        path = os.path.abspath(path)
        if not os.path.isfile(path):
            QMessageBox.warning(self, APP_NAME, f"File not found:\n{path}")
            return False
        self.current_path = path
        self.search_panel.edit.clear()
        err = self.document.load(path)
        if err != QPdfDocument.Error.None_:
            QMessageBox.warning(self, APP_NAME, f"Could not open {os.path.basename(path)}:\n{err.name}")
            self.current_path = None
            self._update_enabled()
            return False
        self.settings.setValue("lastDir", os.path.dirname(path))
        self._add_recent(path)
        return True

    @Slot()
    def close_document(self) -> None:
        self.document.close()
        self.current_path = None
        self.search_panel.edit.clear()
        self.statusBar().showMessage("Document closed.")
        self._update_enabled()

    @Slot()
    def _ask_password(self) -> None:
        pw, ok = QInputDialog.getText(
            self, APP_NAME, "This document is password protected.\nPassword:", QLineEdit.EchoMode.Password
        )
        if ok and pw:
            self.document.setPassword(pw)
        else:
            self.statusBar().showMessage("Password required; document not opened.")

    @Slot()
    def print_dialog(self) -> None:
        if self.document.status() != QPdfDocument.Status.Ready:
            return
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setDocName(os.path.basename(self.current_path or "document.pdf"))
        dlg = QPrintDialog(printer, self)
        dlg.setWindowTitle("Print")
        dlg.setOption(QPrintDialog.PrintDialogOption.PrintPageRange, True)
        dlg.setOption(QPrintDialog.PrintDialogOption.PrintToFile, True)
        dlg.setMinMax(1, self.document.pageCount())
        if dlg.exec() != QPrintDialog.DialogCode.Accepted:
            return
        first, last = page_range_from_printer(printer, self.document.pageCount())
        self.statusBar().showMessage(f"Printing pages {first + 1}–{last + 1}…")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            ok = print_document(self.document, printer, first, last)
        finally:
            QApplication.restoreOverrideCursor()
        self.statusBar().showMessage("Sent to printer." if ok else "Printing failed.")
        if not ok:
            QMessageBox.warning(self, APP_NAME, "Printing failed: the printer could not be started.")

    def goto_page(self, page: int) -> None:
        n = self.document.pageCount()
        if n == 0:
            return
        page = max(0, min(n - 1, page))
        self.navigator.jump(page, QPointF(), self.view.zoomFactor())

    @Slot()
    def prev_page(self) -> None:
        self.goto_page(self.navigator.currentPage() - 1)

    @Slot()
    def next_page(self) -> None:
        self.goto_page(self.navigator.currentPage() + 1)

    @Slot()
    def goto_dialog(self) -> None:
        n = self.document.pageCount()
        page, ok = QInputDialog.getInt(self, "Go to page", f"Page (1–{n}):", self.navigator.currentPage() + 1, 1, n)
        if ok:
            self.goto_page(page - 1)

    def set_zoom(self, factor: float) -> None:
        factor = max(ZOOM_MIN, min(ZOOM_MAX, factor))
        self.view.setZoomMode(QPdfView.ZoomMode.Custom)
        self.view.setZoomFactor(factor)

    @Slot()
    def zoom_in(self) -> None:
        self.set_zoom(self.view.zoomFactor() * ZOOM_STEP)

    @Slot()
    def zoom_out(self) -> None:
        self.set_zoom(self.view.zoomFactor() / ZOOM_STEP)

    @Slot()
    def toggle_page_mode(self) -> None:
        single = self.act_single.isChecked()
        self.view.setPageMode(QPdfView.PageMode.SinglePage if single else QPdfView.PageMode.MultiPage)

    @Slot()
    def toggle_fullscreen(self) -> None:
        if self.act_fullscreen.isChecked():
            self.showFullScreen()
        else:
            self.showNormal()

    # ------------------------------------------------------------- search
    @Slot()
    def focus_search(self) -> None:
        self.sidebar.show()
        self.tabs.setCurrentWidget(self.search_panel)
        self.search_panel.edit.setFocus()
        self.search_panel.edit.selectAll()

    @Slot(str)
    def _search_text_changed(self, text: str) -> None:
        self.search_model.setSearchString(text)
        self.view.setCurrentSearchResultIndex(-1)

    @Slot()
    def _search_return(self) -> None:
        if QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.find_prev()
        else:
            self.find_next()

    def _result_count(self) -> int:
        return self.search_model.rowCount(QModelIndex())

    def show_search_result(self, index: int) -> None:
        count = self._result_count()
        if count == 0:
            self.statusBar().showMessage("No matches.")
            return
        index %= count
        link: QPdfLink = self.search_model.resultAtIndex(index)
        self.view.setCurrentSearchResultIndex(index)
        self.navigator.jump(link.page(), link.location(), self.view.zoomFactor())
        if self.search_panel.results.currentRow() != index:
            self.search_panel.results.blockSignals(True)
            self.search_panel.results.setCurrentRow(index)
            self.search_panel.results.blockSignals(False)
        self.statusBar().showMessage(f"Match {index + 1} of {count} (page {link.page() + 1})")

    @Slot()
    def find_next(self) -> None:
        if not self.search_model.searchString():
            self.focus_search()
            return
        self.show_search_result(self.view.currentSearchResultIndex() + 1)

    @Slot()
    def find_prev(self) -> None:
        if not self.search_model.searchString():
            self.focus_search()
            return
        cur = self.view.currentSearchResultIndex()
        self.show_search_result(cur - 1 if cur > 0 else self._result_count() - 1)

    @Slot(int)
    def _search_result_selected(self, row: int) -> None:
        if row >= 0:
            self.show_search_result(row)

    # ---------------------------------------------------------- bookmarks
    @Slot(QModelIndex)
    def _bookmark_activated(self, index: QModelIndex) -> None:
        if not index.isValid():
            return
        page = index.data(int(QPdfBookmarkModel.Role.Page))
        location = index.data(int(QPdfBookmarkModel.Role.Location))
        zoom = index.data(int(QPdfBookmarkModel.Role.Zoom))
        if page is None or page < 0:
            return
        if not isinstance(location, QPointF):
            location = QPointF()
        if not zoom:
            zoom = self.view.zoomFactor()
        self.navigator.jump(int(page), location, float(zoom))

    # ------------------------------------------------------------- recent
    def _recent(self) -> list[str]:
        value = self.settings.value("recentFiles", [])
        if isinstance(value, str):
            value = [value]
        return [p for p in (value or []) if isinstance(p, str)]

    def _add_recent(self, path: str) -> None:
        recent = [p for p in self._recent() if p != path]
        recent.insert(0, path)
        self.settings.setValue("recentFiles", recent[:MAX_RECENT])
        self._rebuild_recent_menu()

    def _rebuild_recent_menu(self) -> None:
        self.recent_menu.clear()
        recent = self._recent()
        for path in recent:
            a = self.recent_menu.addAction(path)
            a.triggered.connect(lambda _checked=False, p=path: self.open_file(p))
        if recent:
            self.recent_menu.addSeparator()
            clear = self.recent_menu.addAction("Clear list")
            clear.triggered.connect(self._clear_recent)
        self.recent_menu.setEnabled(bool(recent))

    @Slot()
    def _clear_recent(self) -> None:
        self.settings.remove("recentFiles")
        self._rebuild_recent_menu()

    # --------------------------------------------------------------- help
    @Slot()
    def show_about(self) -> None:
        QMessageBox.about(
            self,
            f"About {APP_NAME}",
            f"<h3>{APP_NAME} {__version__}</h3>"
            "<p>You are welcome, Adobe!</p>"
            "<p>Copyright © 2026 Dragos Dimitriu.<br>"
            "Licensed under the GNU General Public License, version 3 or later.<br>"
            "This program comes with ABSOLUTELY NO WARRANTY.</p>"
            "<p>See Help → Credits for the free software this viewer is built on.</p>",
        )

    @Slot()
    def show_credits(self) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("Credits")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(CREDITS_HTML)
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.exec()

    @Slot()
    def show_help(self) -> None:
        QMessageBox.information(self, "Help", HELP_TEXT)

    # --------------------------------------------------------- drag & drop
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if any(u.isLocalFile() and u.toLocalFile().lower().endswith(".pdf") for u in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        for url in event.mimeData().urls():
            if url.isLocalFile() and url.toLocalFile().lower().endswith(".pdf"):
                self.open_file(url.toLocalFile())
                break

    # ------------------------------------------------------------ geometry
    def _restore_geometry(self) -> None:
        geo = self.settings.value("geometry")
        state = self.settings.value("windowState")
        if geo:
            self.restoreGeometry(geo)
        if state:
            self.restoreState(state)

    def closeEvent(self, event) -> None:
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("windowState", self.saveState())
        super().closeEvent(event)


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv)
    QApplication.setApplicationName(APP_ID)
    QApplication.setOrganizationName(ORG_NAME)
    QApplication.setApplicationVersion(__version__)
    app = QApplication(argv)
    window = MainWindow()
    window.show()
    for arg in argv[1:]:
        if not arg.startswith("-"):
            window.open_file(QUrl(arg).toLocalFile() if arg.startswith("file:") else arg)
            break
    return app.exec()
