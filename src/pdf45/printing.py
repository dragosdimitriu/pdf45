# SPDX-License-Identifier: GPL-3.0-or-later
"""Printing support.

Qt's PDF module renders pages to images; QtPrintSupport drives the
printer.  We render each page at the printer's resolution and paint it
scaled to fit the printable area, preserving aspect ratio.  This is
raster printing (like most viewers' "print as image"), which is the
most robust approach across printer drivers.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, QSizeF
from PySide6.QtGui import QImage, QPageLayout, QPainter
from PySide6.QtPdf import QPdfDocument, QPdfDocumentRenderOptions
from PySide6.QtPrintSupport import QPrinter


def _target_rect(page_pt: QSizeF, paper: QRectF) -> QRectF:
    """Largest rectangle with the page's aspect ratio centred in ``paper``."""
    if page_pt.width() <= 0 or page_pt.height() <= 0:
        return paper
    scale = min(paper.width() / page_pt.width(), paper.height() / page_pt.height())
    w = page_pt.width() * scale
    h = page_pt.height() * scale
    x = paper.x() + (paper.width() - w) / 2
    y = paper.y() + (paper.height() - h) / 2
    return QRectF(x, y, w, h)


def print_document(
    document: QPdfDocument,
    printer: QPrinter,
    first_page: int,
    last_page: int,
) -> bool:
    """Print pages ``first_page``..``last_page`` (0-based, inclusive).

    Returns False if the painter could not begin (e.g. the printer is
    unavailable or the output file is not writable).
    """
    page_count = document.pageCount()
    first_page = max(0, first_page)
    last_page = min(page_count - 1, last_page)
    if first_page > last_page:
        return False

    # Choose paper orientation from the first printed page.
    first_size = document.pagePointSize(first_page)
    if first_size.width() > first_size.height():
        printer.setPageOrientation(QPageLayout.Orientation.Landscape)
    else:
        printer.setPageOrientation(QPageLayout.Orientation.Portrait)

    painter = QPainter()
    if not painter.begin(printer):
        return False
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    options = QPdfDocumentRenderOptions()
    try:
        for page in range(first_page, last_page + 1):
            if page != first_page:
                printer.newPage()
            # The painter's origin is the top-left of the printable area,
            # so only the size of pageRect matters here.
            paper_full = printer.pageRect(QPrinter.Unit.DevicePixel)
            paper = QRectF(0, 0, paper_full.width(), paper_full.height())
            page_pt = document.pagePointSize(page)
            target = _target_rect(page_pt, paper)
            # Render at the device pixel size of the target rectangle.
            size = QSize(max(1, round(target.width())), max(1, round(target.height())))
            image: QImage = document.render(page, size, options)
            if image.isNull():
                continue
            painter.drawImage(target, image, QRectF(image.rect()))
    finally:
        painter.end()
    return True


def page_range_from_printer(printer: QPrinter, page_count: int) -> tuple[int, int]:
    """Translate the dialog's 1-based from/to (0 = all) into 0-based indices."""
    if printer.printRange() == QPrinter.PrintRange.PageRange and printer.fromPage() > 0:
        return printer.fromPage() - 1, printer.toPage() - 1
    return 0, page_count - 1


__all__ = ["print_document", "page_range_from_printer"]
