# SPDX-License-Identifier: GPL-3.0-or-later
"""Headless smoke tests.

Run with:  QT_QPA_PLATFORM=offscreen pytest
(conftest.py sets the platform automatically.)
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from PySide6.QtCore import QModelIndex
from PySide6.QtPdf import QPdfBookmarkModel, QPdfDocument
from PySide6.QtPrintSupport import QPrinter
from PySide6.QtWidgets import QApplication

from pdf45.app import MainWindow
from pdf45.printing import print_document

FIXTURE = Path(__file__).parent / "fixtures" / "sample.pdf"


def wait_until(app: QApplication, predicate, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app):
    w = MainWindow()
    w.show()
    app.processEvents()
    yield w
    w.close()
    app.processEvents()


def test_open_document(app, window):
    assert window.open_file(str(FIXTURE))
    assert wait_until(app, lambda: window.document.status() == QPdfDocument.Status.Ready)
    assert window.document.pageCount() == 3
    assert window.page_spin.maximum() == 3
    assert window.act_print.isEnabled()


def test_navigation(app, window):
    window.open_file(str(FIXTURE))
    wait_until(app, lambda: window.document.status() == QPdfDocument.Status.Ready)
    window.next_page()
    app.processEvents()
    assert window.navigator.currentPage() == 1
    window.goto_page(99)  # clamped
    app.processEvents()
    assert window.navigator.currentPage() == 2
    window.prev_page()
    app.processEvents()
    assert window.navigator.currentPage() == 1


def test_bookmarks(app, window):
    window.open_file(str(FIXTURE))
    wait_until(app, lambda: window.document.status() == QPdfDocument.Status.Ready)
    model = window.bookmark_model
    assert wait_until(app, lambda: model.rowCount(QModelIndex()) == 3)
    titles = [model.index(i, 0).data() for i in range(3)]
    assert titles == ["Chapter One", "Chapter Two", "Chapter Three"]
    third = model.index(2, 0)
    assert third.data(int(QPdfBookmarkModel.Role.Page)) == 2
    window._bookmark_activated(third)
    app.processEvents()
    assert window.navigator.currentPage() == 2


def test_search(app, window):
    window.open_file(str(FIXTURE))
    wait_until(app, lambda: window.document.status() == QPdfDocument.Status.Ready)
    window.search_panel.edit.setText("needle")
    # The model searches pages incrementally; wait for all three hits.
    assert wait_until(app, lambda: window.search_model.rowCount(QModelIndex()) == 3)
    pages = [window.search_model.resultAtIndex(i).page() for i in range(3)]
    assert pages == [0, 2, 2]
    window.find_next()
    app.processEvents()
    assert window.view.currentSearchResultIndex() == 0
    window.find_next()
    app.processEvents()
    assert window.view.currentSearchResultIndex() == 1
    assert window.navigator.currentPage() == 2
    window.find_prev()
    app.processEvents()
    assert window.view.currentSearchResultIndex() == 0
    assert wait_until(app, lambda: window.search_panel.results.count() == 3)


def test_print_to_pdf(app, window, tmp_path):
    pypdf = pytest.importorskip("pypdf")
    window.open_file(str(FIXTURE))
    wait_until(app, lambda: window.document.status() == QPdfDocument.Status.Ready)
    out = tmp_path / "printed.pdf"
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(str(out))
    assert print_document(window.document, printer, 1, 2)
    assert out.exists() and out.stat().st_size > 1000
    assert len(pypdf.PdfReader(str(out)).pages) == 2


def test_help_and_credits_text():
    from pdf45.credits import CREDITS, CREDITS_HTML, HELP_TEXT

    assert HELP_TEXT == "You are welcome, Adobe!"
    assert len(CREDITS) >= 8
    for name, licence, url, _ in CREDITS:
        assert name in CREDITS_HTML and url in CREDITS_HTML and licence
