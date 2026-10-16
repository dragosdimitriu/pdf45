# SPDX-License-Identifier: GPL-3.0-or-later
"""Show visible signatures on the page.

Qt's PDF module renders pages without their form fields, so the appearance
of a visible signature (a signature *field*) never shows up in QPdfView.
This view draws those appearances on top: each one is rendered by PDFium
through pypdfium2 with its form environment enabled, cropped to the
signature rectangle, and painted where QPdfView placed the page.

The page placement below mirrors QPdfView's own document layout
(QPdfViewPrivate::calculateDocumentLayout).
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPen
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView

FRAME = {"ok": QColor("#1e8e3e"), "warn": QColor("#e37400"), "bad": QColor("#d93025")}


class SignedPdfView(QPdfView):
    """QPdfView that also paints visible signature appearances."""

    signatureClicked = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sigs: list[dict] = []
        self._pdfium = None
        self._path: str | None = None
        self._password: str | None = None
        self._cache: dict[tuple[int, int], QImage] = {}
        self.viewport().setMouseTracking(True)

    # ------------------------------------------------------------- data
    def set_signatures(self, path: str | None, password: str | None, results) -> None:
        """``results``: SignatureInfo list from verify.py (only visible ones are drawn)."""
        from pdf45.signing.sign import page_geometry
        from pdf45.signing.ui import verdict_kind

        if path != self._path or password != self._password:
            self._close_pdfium()
            self._path, self._password = path, password
        self._sigs = []
        for i, r in enumerate(results or []):
            if r.page is None or r.rect is None:
                continue
            try:
                geom = page_geometry(path, r.page, password)
            except Exception:
                continue
            x1, y1, x2, y2 = r.rect
            ax, ay = geom.to_display(x1, y1)
            bx, by = geom.to_display(x2, y2)
            self._sigs.append({
                "index": i,
                "page": r.page,
                "display": QRectF(min(ax, bx), min(ay, by), abs(bx - ax), abs(by - ay)),
                "page_size": geom.display_size,
                "kind": verdict_kind(r.verdict),
                "tip": f"{r.signer} – {r.verdict.value}",
            })
        self.viewport().update()

    def signature_location(self, index: int) -> tuple[int, QPointF] | None:
        """Page and top-left point (in points) of a visible signature, for navigation."""
        for sig in self._sigs:
            if sig["index"] == index:
                d = sig["display"]
                return sig["page"], QPointF(max(0.0, d.x() - 36), max(0.0, d.y() - 72))
        return None

    def clear_signatures(self) -> None:
        self._sigs = []
        self._close_pdfium()
        self._path = self._password = None
        self.viewport().update()

    def _close_pdfium(self) -> None:
        self._cache.clear()
        if self._pdfium is not None:
            try:
                self._pdfium.close()
            except Exception:
                pass
        self._pdfium = None

    # ----------------------------------------------------------- layout
    def page_rects(self) -> dict[int, QRect]:
        """Where QPdfView draws each page, in viewport coordinates."""
        doc: QPdfDocument | None = self.document()
        if doc is None or doc.status() != QPdfDocument.Status.Ready:
            return {}
        screen = QGuiApplication.primaryScreen()
        res = (screen.logicalDotsPerInch() if screen else 96.0) / 72.0
        margins = self.documentMargins()
        vp = self.viewport().size()
        single = self.pageMode() == QPdfView.PageMode.SinglePage
        current = self.pageNavigator().currentPage()
        pages = range(current, current + 1) if single else range(doc.pageCount())
        mode = self.zoomMode()

        sizes: dict[int, QSize] = {}
        total_w = 0
        for p in pages:
            pt = doc.pagePointSize(p)
            base = QSize(int(pt.width() * res), int(pt.height() * res))
            if mode == QPdfView.ZoomMode.Custom:
                size = QSize(int(pt.width() * res * self.zoomFactor()), int(pt.height() * res * self.zoomFactor()))
            elif mode == QPdfView.ZoomMode.FitToWidth:
                scale = (vp.width() - margins.left() - margins.right()) / max(1, base.width())
                size = QSize(round(base.width() * scale), round(base.height() * scale))
            else:  # FitInView
                avail = QSize(vp.width() - margins.left() - margins.right(), vp.height() - self.pageSpacing())
                size = base.scaled(avail, Qt.AspectRatioMode.KeepAspectRatio)
            sizes[p] = size
            total_w = max(total_w, size.width())
        total_w += margins.left() + margins.right()

        dx = self.horizontalScrollBar().value()
        dy = self.verticalScrollBar().value()
        rects: dict[int, QRect] = {}
        y = margins.top()
        for p in pages:
            s = sizes[p]
            x = (max(total_w, vp.width()) - s.width()) // 2
            rects[p] = QRect(x - dx, y - dy, s.width(), s.height())
            y += s.height() + self.pageSpacing()
        return rects

    def _signature_rects(self) -> list[tuple[dict, QRectF, float]]:
        out = []
        if not self._sigs:
            return out
        pages = self.page_rects()
        for sig in self._sigs:
            pr = pages.get(sig["page"])
            if pr is None:
                continue
            w_pt, _h_pt = sig["page_size"]
            scale = pr.width() / w_pt if w_pt else 1.0
            d = sig["display"]
            out.append((sig, QRectF(pr.x() + d.x() * scale, pr.y() + d.y() * scale,
                                    d.width() * scale, d.height() * scale), scale))
        return out

    # ---------------------------------------------------------- drawing
    def _page_image(self, page: int, scale: float) -> QImage | None:
        key = (page, round(scale * 20))
        if key in self._cache:
            return self._cache[key]
        try:
            import pypdfium2 as pdfium

            if self._pdfium is None:
                self._pdfium = pdfium.PdfDocument(self._path, password=self._password)
                self._pdfium.init_forms()
            bitmap = self._pdfium[page].render(scale=scale, may_draw_forms=True)
            img = bitmap.to_pil().convert("RGB")
            qimg = QImage(img.tobytes(), img.width, img.height, img.width * 3, QImage.Format.Format_RGB888).copy()
        except Exception:
            qimg = None
        if len(self._cache) > 6:
            self._cache.clear()
        self._cache[key] = qimg
        return qimg

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        rects = self._signature_rects()
        if not rects:
            return
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        dpr = self.devicePixelRatioF()
        for sig, r, scale in rects:
            if not r.intersects(QRectF(self.viewport().rect())):
                continue
            img = self._page_image(sig["page"], scale * dpr)
            if img is not None:
                d = sig["display"]
                k = scale * dpr
                src = QRectF(d.x() * k, d.y() * k, d.width() * k, d.height() * k)
                painter.drawImage(r, img, src)
            painter.setPen(QPen(FRAME[sig["kind"]], 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(r.adjusted(-1, -1, 1, 1))
        painter.end()

    # ----------------------------------------------------------- events
    def _hit(self, pos) -> dict | None:
        for sig, r, _ in self._signature_rects():
            if r.contains(pos):
                return sig
        return None

    def mousePressEvent(self, event) -> None:
        sig = self._hit(event.position())
        if sig is not None and event.button() == Qt.MouseButton.LeftButton:
            self.signatureClicked.emit(sig["index"])
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        sig = self._hit(event.position())
        self.viewport().setCursor(Qt.CursorShape.PointingHandCursor if sig else Qt.CursorShape.ArrowCursor)
        self.viewport().setToolTip(sig["tip"] if sig else "")
        super().mouseMoveEvent(event)
