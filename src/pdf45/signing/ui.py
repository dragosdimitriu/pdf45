# SPDX-License-Identifier: GPL-3.0-or-later
"""Qt user interface for electronic signatures.

* SignDialog       - choose certificate, enter PIN, reason, placement, TSA/LTV;
* PlacementDialog  - draw the visible signature's rectangle on a page;
* SignaturesPanel  - list of signatures in the open document and their status;
* SettingsDialog   - token drivers, timestamp server, defaults, trust lists.

Token access, signing and validation run in worker threads (they talk to
hardware and to the network); this module never blocks the GUI thread.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSettings, QSize, QStandardPaths, Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPen, QPixmap
from PySide6.QtPdf import QPdfDocument
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pdf45.signing.drivers import installed_drivers, known_drivers

# ----------------------------------------------------------------- settings
KEY = "signing/"


class SigningSettings:
    """Typed access to the [signing] QSettings group."""

    def __init__(self, qs: QSettings):
        self.qs = qs

    def _get(self, key, default):
        v = self.qs.value(KEY + key, default)
        if isinstance(default, bool):
            return v in (True, "true", "1", 1)
        if isinstance(default, list):
            if v in (None, ""):
                return []
            return [v] if isinstance(v, str) else list(v)
        return v if v is not None else default

    def _set(self, key, value):
        self.qs.setValue(KEY + key, value)

    extra_drivers = property(lambda s: s._get("extraDrivers", []), lambda s, v: s._set("extraDrivers", v))
    use_timestamp = property(lambda s: s._get("useTimestamp", True), lambda s, v: s._set("useTimestamp", v))
    tsa_url = property(lambda s: s._get("tsaUrl", _default_tsa()), lambda s, v: s._set("tsaUrl", v))
    tsa_user = property(lambda s: s._get("tsaUser", ""), lambda s, v: s._set("tsaUser", v))
    ltv = property(lambda s: s._get("ltv", True), lambda s, v: s._set("ltv", v))
    visible = property(lambda s: s._get("visible", True), lambda s, v: s._set("visible", v))
    reason = property(lambda s: s._get("reason", ""), lambda s, v: s._set("reason", v))
    location = property(lambda s: s._get("location", ""), lambda s, v: s._set("location", v))
    contact = property(lambda s: s._get("contact", ""), lambda s, v: s._set("contact", v))
    appearance_lang = property(lambda s: s._get("appearanceLang", "en"), lambda s, v: s._set("appearanceLang", v))
    online_checks = property(lambda s: s._get("onlineChecks", True), lambda s, v: s._set("onlineChecks", v))
    last_cert = property(lambda s: s._get("lastCert", ""), lambda s, v: s._set("lastCert", v))


def _default_tsa() -> str:
    from pdf45.signing.sign import DEFAULT_TSA

    return DEFAULT_TSA


_TRUST = {"store": None}


def trust_store(settings: SigningSettings):
    """One TrustStore per process, over the trusted lists of all EU member states.

    All of them are needed even for Romanian signers: the default timestamp
    server (Sectigo) is a Spanish qualified trust service.
    """
    from pdf45.signing.trust import TrustStore

    if _TRUST["store"] is None:
        cache = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))
        _TRUST["store"] = TrustStore(cache / "trusted-lists", None)
    return _TRUST["store"]


def logo_path() -> str | None:
    from pdf45.app import _resource_path

    p = _resource_path("pdf45.png")
    return str(p) if p.is_file() else None


def font_path() -> str | None:
    from pdf45.app import _resource_path

    p = _resource_path("fonts/DejaVuSans.ttf")
    return str(p) if p.is_file() else None


# ------------------------------------------------------------------- worker
class Worker(QThread):
    """Run ``fn(*args, progress=callback)`` in a thread."""

    done = Signal(object)
    failed = Signal(str)
    progress = Signal(str)
    partial = Signal(object)

    def __init__(self, fn, *args, parent=None):
        super().__init__(parent)
        self.fn = fn
        self.args = args
        self.finished.connect(self.deleteLater)

    def run(self) -> None:
        from pdf45.signing.sign import SigningError
        from pdf45.signing.token import TokenError

        try:
            result = self.fn(*self.args, progress=self.progress.emit, partial=self.partial.emit)
        except (TokenError, SigningError) as e:
            self.failed.emit(str(e))
        except Exception as e:  # unexpected: show enough to report a bug
            self.failed.emit(f"{e.__class__.__name__}: {e}\n\n{traceback.format_exc(limit=4)}")
        else:
            self.done.emit(result)


def _status_icon(kind: str, size: int = 16) -> QIcon:
    colors = {"ok": "#1e8e3e", "warn": "#e37400", "bad": "#d93025", "busy": "#5f6368"}
    marks = {"ok": "✓", "warn": "!", "bad": "✕", "busy": "…"}
    pm = QPixmap(size * 2, size * 2)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor(colors[kind]))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(1, 1, size * 2 - 2, size * 2 - 2)
    p.setPen(QColor("white"))
    f = QFont()
    f.setBold(True)
    f.setPixelSize(int(size * 1.3))
    p.setFont(f)
    p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, marks[kind])
    p.end()
    return QIcon(pm)


def verdict_kind(verdict) -> str:
    return "ok" if verdict.ok else ("bad" if verdict.bad else "warn")


# --------------------------------------------------------------- placement
class PageCanvas(QWidget):
    """Shows a rendered page and lets the user drag a rectangle on it."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.image = QImage()
        self.rect = QRectF()  # in widget pixels
        self._origin: QPointF | None = None
        self.setCursor(Qt.CursorShape.CrossCursor)

    def set_image(self, image: QImage) -> None:
        self.image = image
        self.setFixedSize(image.size())
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._origin = e.position()
            self.rect = QRectF(self._origin, self._origin)
            self.update()

    def mouseMoveEvent(self, e):
        if self._origin is not None:
            p = e.position()
            p.setX(max(0.0, min(p.x(), self.width() - 1.0)))
            p.setY(max(0.0, min(p.y(), self.height() - 1.0)))
            self.rect = QRectF(self._origin, p).normalized()
            self.update()

    def mouseReleaseEvent(self, e):
        if self._origin is not None:
            self._origin = None
            if self.rect.width() < 12 or self.rect.height() < 8:
                self.rect = QRectF()
            self.update()
            self.changed.emit()

    def paintEvent(self, _e):
        p = QPainter(self)
        if not self.image.isNull():
            p.drawImage(0, 0, self.image)
        if not self.rect.isNull():
            p.setPen(QPen(QColor("#1a73e8"), 2))
            p.setBrush(QColor(26, 115, 232, 50))
            p.drawRect(self.rect)
            p.setPen(QColor("#1a73e8"))
            p.drawText(self.rect.adjusted(6, 4, -4, -4),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, "Signature")
        p.end()


class PlacementDialog(QDialog):
    """Choose the page and the rectangle of a visible signature."""

    MAX_W, MAX_H = 760, 860

    def __init__(self, document: QPdfDocument, path: str, password: str | None, page: int,
                 box: tuple[float, float, float, float] | None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Place the signature")
        self.document = document
        self.path = path
        self.password = password
        self.geom = None
        self.scale = 1.0
        self.result_page = page
        self.result_box = box

        self.page_spin = QSpinBox(self)
        self.page_spin.setRange(1, max(1, document.pageCount()))
        self.page_spin.setValue(page + 1)
        self.page_spin.valueChanged.connect(lambda v: self._load_page(v - 1, keep_rect=True))
        last = QPushButton("Last page", self)
        last.clicked.connect(lambda: self.page_spin.setValue(self.page_spin.maximum()))

        top = QHBoxLayout()
        top.addWidget(QLabel("Page:"))
        top.addWidget(self.page_spin)
        top.addWidget(QLabel(f"of {document.pageCount()}"))
        top.addWidget(last)
        top.addStretch(1)
        top.addWidget(QLabel("Drag a rectangle where the signature should appear."))

        self.canvas = PageCanvas()
        self.canvas.changed.connect(self._update_ok)
        scroll = QScrollArea(self)
        scroll.setWidget(self.canvas)
        scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scroll.setMinimumSize(QSize(560, 520))

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(scroll, 1)
        lay.addWidget(self.buttons)
        self._load_page(page, keep_rect=False, initial_box=box)

    def _load_page(self, index: int, keep_rect: bool, initial_box=None) -> None:
        from pdf45.signing.sign import page_geometry

        old_rect = QRectF(self.canvas.rect) if keep_rect else QRectF()
        old_scale = self.scale
        self.geom = page_geometry(self.path, index, self.password)
        w_pt, h_pt = self.geom.display_size
        self.scale = min(self.MAX_W / w_pt, self.MAX_H / h_pt)
        size = QSize(max(1, int(w_pt * self.scale)), max(1, int(h_pt * self.scale)))
        self.canvas.set_image(self.document.render(index, size))
        if initial_box is not None:
            self.canvas.rect = self._box_to_rect(initial_box)
        elif keep_rect and not old_rect.isNull():
            f = self.scale / old_scale
            self.canvas.rect = QRectF(old_rect.x() * f, old_rect.y() * f, old_rect.width() * f, old_rect.height() * f)
        else:
            # Suggest the bottom-right corner (200 x 70 pt, 36 pt margin).
            s = self.scale
            self.canvas.rect = QRectF((w_pt - 236) * s, (h_pt - 106) * s, 200 * s, 70 * s)
        self.canvas.update()
        self._update_ok()

    def _box_to_rect(self, box) -> QRectF:
        # Inverse mapping for the unrotated case is enough to re-show a previous choice.
        g, s = self.geom, self.scale
        if g.rotate != 0:
            return QRectF()
        x1, y1, x2, y2 = box
        return QRectF((x1 - g.llx) * s, (g.ury - y2) * s, (x2 - x1) * s, (y2 - y1) * s)

    def _update_ok(self) -> None:
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(not self.canvas.rect.isNull())

    def _accept(self) -> None:
        r, s = self.canvas.rect, self.scale
        self.result_page = self.page_spin.value() - 1
        self.result_box = self.geom.box_from_display(r.left() / s, r.top() / s, r.right() / s, r.bottom() / s)
        self.accept()


# ------------------------------------------------------------- sign dialog
def _load_certs(extra_drivers, progress=None, partial=None):
    from pdf45.signing.token import list_signing_certificates

    return list_signing_certificates(installed_drivers(extra_drivers))


def _sign(req, trust, progress=None, partial=None):
    from pdf45.signing.sign import sign_document

    return sign_document(req, trust, progress or (lambda _m: None))


class SignDialog(QDialog):
    """Collects everything needed for a signature, then signs in a worker thread."""

    def __init__(self, document: QPdfDocument, path: str, password: str | None,
                 settings: SigningSettings, current_page: int, parent=None):
        super().__init__(parent)
        from pdf45.signing.sign import default_output_path, existing_signature_fields

        self.setWindowTitle("Sign document")
        self.setMinimumWidth(620)
        self.document = document
        self.path = path
        self.password = password
        self.settings = settings
        self.certs = []
        self.page = current_page
        self.box = None
        self.output_path = None
        self.level = None
        self._worker = None
        try:
            self.empty_fields, _ = existing_signature_fields(path, password)
        except Exception:
            self.empty_fields = []

        # certificate ---------------------------------------------------
        self.cert_box = QComboBox(self)
        self.cert_box.currentIndexChanged.connect(self._cert_changed)
        refresh = QPushButton("Refresh", self)
        refresh.setToolTip("Look for tokens again (after inserting one)")
        refresh.clicked.connect(self.reload_certs)
        cert_row = QHBoxLayout()
        cert_row.addWidget(self.cert_box, 1)
        cert_row.addWidget(refresh)
        self.cert_info = QLabel(self)
        self.cert_info.setWordWrap(True)
        self.cert_info.setTextFormat(Qt.TextFormat.RichText)
        self.pin = QLineEdit(self)
        self.pin.setEchoMode(QLineEdit.EchoMode.Password)
        self.pin.setPlaceholderText("Token PIN")
        self.pin.returnPressed.connect(self._sign_clicked)

        g_cert = QGroupBox("Certificate (hardware token)", self)
        f = QFormLayout(g_cert)
        f.addRow("Certificate:", cert_row)
        f.addRow("", self.cert_info)
        f.addRow("PIN:", self.pin)

        # details --------------------------------------------------------
        self.reason = QLineEdit(settings.reason, self)
        self.reason.setPlaceholderText("e.g. I approve this document")
        self.location = QLineEdit(settings.location, self)
        self.location.setPlaceholderText("e.g. Bucharest")
        self.contact = QLineEdit(settings.contact, self)
        self.contact.setPlaceholderText("optional (e-mail / phone)")
        g_det = QGroupBox("Details", self)
        f = QFormLayout(g_det)
        f.addRow("Reason:", self.reason)
        f.addRow("Location:", self.location)
        f.addRow("Contact:", self.contact)

        # appearance -----------------------------------------------------
        self.rb_invisible = QRadioButton("Invisible (only in the signatures panel)", self)
        self.rb_visible = QRadioButton("Visible on the page", self)
        self.rb_field = QRadioButton("In an existing signature field:", self)
        grp = QButtonGroup(self)
        for rb in (self.rb_invisible, self.rb_visible, self.rb_field):
            grp.addButton(rb)
            rb.toggled.connect(self._update_state)
        self.place_btn = QPushButton("Choose position…", self)
        self.place_btn.clicked.connect(self.choose_position)
        self.place_label = QLabel("", self)
        self.field_box = QComboBox(self)
        self.field_box.addItems(self.empty_fields)
        self.lang_box = QComboBox(self)
        self.lang_box.addItem("English", "en")
        self.lang_box.addItem("Română", "ro")
        self.lang_box.setCurrentIndex(max(0, self.lang_box.findData(settings.appearance_lang)))
        if self.empty_fields:
            self.rb_field.setChecked(True)
        elif settings.visible:
            self.rb_visible.setChecked(True)
        else:
            self.rb_invisible.setChecked(True)
        self.rb_field.setEnabled(bool(self.empty_fields))

        g_app = QGroupBox("Appearance", self)
        v = QVBoxLayout(g_app)
        v.addWidget(self.rb_invisible)
        row = QHBoxLayout()
        row.addWidget(self.rb_visible)
        row.addWidget(self.place_btn)
        row.addWidget(self.place_label, 1)
        v.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(self.rb_field)
        row.addWidget(self.field_box, 1)
        v.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("Text language:"))
        row.addWidget(self.lang_box)
        row.addStretch(1)
        v.addLayout(row)

        # long-term ------------------------------------------------------
        self.cb_ts = QCheckBox("Add a trusted timestamp", self)
        self.cb_ts.setChecked(settings.use_timestamp)
        self.ts_label = QLabel(self)
        self.ts_label.setStyleSheet("color: palette(mid)")
        self.tsa_pw = QLineEdit(self)
        self.tsa_pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.tsa_pw.setPlaceholderText(f"password for timestamp user {settings.tsa_user}")
        self.tsa_pw.setVisible(bool(settings.tsa_user))
        self.cb_ltv = QCheckBox("Long-term validation (embed certificate chain and revocation data)", self)
        self.cb_ltv.setChecked(settings.ltv)
        self.cb_ts.toggled.connect(self._update_state)
        g_lt = QGroupBox("Validity over time", self)
        v = QVBoxLayout(g_lt)
        row = QHBoxLayout()
        row.addWidget(self.cb_ts)
        row.addWidget(self.ts_label, 1)
        v.addLayout(row)
        v.addWidget(self.tsa_pw)
        v.addWidget(self.cb_ltv)

        # output ---------------------------------------------------------
        self.out_edit = QLineEdit(default_output_path(path), self)
        browse = QPushButton("Browse…", self)
        browse.clicked.connect(self._browse)
        row_out = QHBoxLayout()
        row_out.addWidget(self.out_edit, 1)
        row_out.addWidget(browse)
        g_out = QGroupBox("Save signed document as", self)
        g_out.setLayout(row_out)

        self.status = QLabel("", self)
        self.status.setWordWrap(True)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel, self)
        self.sign_btn = self.buttons.addButton("Sign", QDialogButtonBox.ButtonRole.AcceptRole)
        self.sign_btn.setDefault(True)
        self.buttons.accepted.connect(self._sign_clicked)
        self.buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        for w in (g_cert, g_det, g_app, g_lt, g_out, self.status, self.buttons):
            lay.addWidget(w)

        self._update_state()
        self.reload_certs()

    # ------------------------------------------------------------ certs
    def reload_certs(self) -> None:
        self.cert_box.clear()
        self.cert_box.addItem("Looking for tokens…")
        self.cert_box.setEnabled(False)
        self.cert_info.setText("")
        w = Worker(_load_certs, self.settings.extra_drivers, parent=self)
        w.done.connect(self._certs_loaded)
        w.failed.connect(lambda msg: self._certs_loaded(([], [msg])))
        w.start()

    def _certs_loaded(self, result) -> None:
        certs, problems = result
        self.certs = certs
        self.cert_box.clear()
        if not certs:
            drivers = installed_drivers(self.settings.extra_drivers)
            if not drivers:
                hint = ("No token driver (PKCS#11 module) was found. Install your token's software "
                        "(e.g. SafeNet Authentication Client) or add the driver in Signature → Settings.")
            else:
                hint = "No certificate found. Insert the token and press Refresh."
            if problems:
                hint += "<br><small>" + "<br>".join(_html(p) for p in problems) + "</small>"
            self.cert_box.addItem("(no certificate)")
            self.cert_info.setText(hint)
            self.cert_box.setEnabled(False)
        else:
            for c in certs:
                self.cert_box.addItem(c.describe())
            last = self.settings.last_cert
            for i, c in enumerate(certs):
                if last and c.cert.sha256.hex() == last:
                    self.cert_box.setCurrentIndex(i)
                    break
            self.cert_box.setEnabled(True)
            self._cert_changed(self.cert_box.currentIndex())
            self.pin.setFocus()
        self._update_state()

    def current_cert(self):
        i = self.cert_box.currentIndex()
        return self.certs[i] if 0 <= i < len(self.certs) else None

    def _cert_changed(self, _i) -> None:
        c = self.current_cert()
        if c is None:
            return
        lines = [
            f"<b>{_html(c.subject_cn)}</b>",
            f"Issued by: {_html(c.issuer_cn)}",
            f"Valid: {c.not_before:%Y-%m-%d} – {c.not_after:%Y-%m-%d}"
            + ("" if c.is_valid_now else " <span style='color:#d93025'><b>(not valid now)</b></span>"),
            f"Token: {_html(c.token_label)} ({_html(c.token_model)}) · {_html(c.driver.name)}",
        ]
        if c.is_qualified:
            lines.append("<span style='color:#1e8e3e'>Qualified certificate (eIDAS)</span>")
        warn = c.pin_warning()
        if warn:
            lines.append(f"<span style='color:#d93025'><b>{_html(warn)}</b></span>")
        self.cert_info.setText("<br>".join(lines))
        self.pin.setEnabled(not c.protected_auth)
        self.pin.setPlaceholderText("Enter the PIN on the token's keypad" if c.protected_auth else "Token PIN")
        self._update_state()

    # --------------------------------------------------------- appearance
    def choose_position(self) -> None:
        dlg = PlacementDialog(self.document, self.path, self.password, self.page, self.box, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.page, self.box = dlg.result_page, dlg.result_box
            self._box_is_default = False
            self.rb_visible.setChecked(True)
        self._update_state()

    def _default_box(self) -> None:
        """Bottom-right corner of the current page, 200 x 70 pt with a 36 pt margin."""
        from pdf45.signing.sign import page_geometry

        try:
            g = page_geometry(self.path, self.page, self.password)
        except Exception:
            return
        w, h = g.display_size
        self.box = g.box_from_display(w - 236, h - 106, w - 36, h - 36)
        self._box_is_default = True

    def _update_state(self) -> None:
        if not hasattr(self, "sign_btn"):
            return  # still building the dialog
        if self.rb_visible.isChecked() and self.box is None:
            self._default_box()
        if self.box is not None:
            x1, y1, x2, y2 = self.box
            where = "bottom right, " if getattr(self, "_box_is_default", False) else ""
            self.place_label.setText(f"page {self.page + 1}, {where}{x2 - x1:.0f} × {y2 - y1:.0f} pt")
        else:
            self.place_label.setText("<span style='color:#e37400'>no position chosen yet</span>")
        self.place_btn.setEnabled(self.rb_visible.isChecked())
        self.field_box.setEnabled(self.rb_field.isChecked())
        self.lang_box.setEnabled(not self.rb_invisible.isChecked())
        self.ts_label.setText(self.settings.tsa_url if self.cb_ts.isChecked() else "")
        self.tsa_pw.setEnabled(self.cb_ts.isChecked())
        c = self.current_cert()
        ready = c is not None and c.is_valid_now and not (self.rb_visible.isChecked() and self.box is None)
        self.sign_btn.setEnabled(ready and self._worker is None)

    def _browse(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save signed document", self.out_edit.text(),
                                              "PDF documents (*.pdf)")
        if path:
            self.out_edit.setText(path)

    # -------------------------------------------------------------- sign
    def _sign_clicked(self) -> None:
        from pdf45.signing.sign import SignRequest

        c = self.current_cert()
        if c is None or not self.sign_btn.isEnabled():
            return
        if not c.protected_auth and not self.pin.text():
            self.status.setText("<span style='color:#d93025'>Enter the token PIN.</span>")
            self.pin.setFocus()
            return
        out = self.out_edit.text().strip()
        if not out:
            self.status.setText("<span style='color:#d93025'>Choose where to save the signed document.</span>")
            return
        if not out.lower().endswith(".pdf"):
            out += ".pdf"
        if Path(out).resolve() == Path(self.path).resolve():
            self.status.setText("<span style='color:#d93025'>Save the signed copy under a different name.</span>")
            return
        if Path(out).exists() and QMessageBox.question(
                self, "Sign document", f"{out}\nalready exists. Replace it?") != QMessageBox.StandardButton.Yes:
            return
        visible = not self.rb_invisible.isChecked()
        req = SignRequest(
            input_path=self.path,
            output_path=out,
            token_cert=c,
            pin=self.pin.text() or None,
            reason=self.reason.text().strip(),
            location=self.location.text().strip(),
            contact=self.contact.text().strip(),
            visible=visible,
            page=self.page,
            box=self.box if self.rb_visible.isChecked() else None,
            existing_field=self.field_box.currentText() if self.rb_field.isChecked() else None,
            timestamp_url=self.settings.tsa_url if self.cb_ts.isChecked() else None,
            tsa_user=self.settings.tsa_user,
            tsa_password=self.tsa_pw.text(),
            ltv=self.cb_ltv.isChecked(),
            appearance_lang=self.lang_box.currentData(),
            font_path=font_path(),
            logo_path=logo_path(),
            pdf_password=self.password,
        )
        # Remember the choices (never the PIN).
        s = self.settings
        s.reason, s.location, s.contact = req.reason, req.location, req.contact
        s.visible = self.rb_visible.isChecked() or self.rb_field.isChecked()
        s.use_timestamp, s.ltv = self.cb_ts.isChecked(), req.ltv
        s.appearance_lang = req.appearance_lang
        s.last_cert = c.cert.sha256.hex()

        self.output_path = out
        self._set_busy(True)
        self._worker = Worker(_sign, req, trust_store(self.settings), parent=self)
        self._worker.progress.connect(lambda m: self.status.setText(_html(m)))
        self._worker.done.connect(self._signed)
        self._worker.failed.connect(self._sign_failed)
        self._worker.start()

    def _set_busy(self, busy: bool) -> None:
        for w in self.findChildren(QWidget):
            if isinstance(w, (QLineEdit, QComboBox, QPushButton, QCheckBox, QRadioButton, QSpinBox)):
                w.setEnabled(not busy)
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setEnabled(not busy)
        if not busy:
            self._worker = None
            self.rb_field.setEnabled(bool(self.empty_fields))
            self._update_state()
            self._cert_changed(self.cert_box.currentIndex())

    def _signed(self, level: str) -> None:
        self.pin.clear()
        self.level = level
        self._worker = None
        self.accept()

    def _sign_failed(self, msg: str) -> None:
        self.pin.clear()
        self._set_busy(False)
        self.status.setText("")
        QMessageBox.critical(self, "Signing failed", msg)
        self.pin.setFocus()

    def reject(self) -> None:
        if self._worker is not None:
            return  # cannot abort a signature in progress on the token
        self.pin.clear()
        super().reject()


def _html(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


# --------------------------------------------------------- signatures panel
def verify_with_eu(path, password, settings_snapshot, trust, progress=None, partial=None):
    """Validate quickly without the EU lists if they are not loaded yet, then with them."""
    from pdf45.signing.verify import count_signatures, verify_document

    progress = progress or (lambda _m: None)
    if count_signatures(path, password) == 0:
        return {"path": path, "results": [], "eu": False}
    online = settings_snapshot["online"]
    if not trust.eu.loaded:
        progress("Checking signatures…")
        first = verify_document(path, trust, use_eu=False, online=online, password=password)
        if partial:
            partial({"path": path, "results": first, "eu": False})
        progress("Loading EU trusted lists (the first time this takes about a minute)…")
    else:
        progress("Checking signatures…")
    results = verify_document(path, trust, use_eu=True, online=online, password=password)
    return {"path": path, "results": results, "eu": trust.eu.loaded, "eu_error": trust.eu.error}


class SignaturesPanel(QWidget):
    signatureActivated = Signal(int)  # index into the results
    recheckRequested = Signal()
    signRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.summary = QLabel("No document.", self)
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.TextFormat.RichText)
        self.tree = QTreeWidget(self)
        self.tree.setHeaderLabels(["Signed by", "Status"])
        self.tree.setRootIsDecorated(False)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.currentItemChanged.connect(self._selected)
        self.tree.itemDoubleClicked.connect(self._activated)
        self.details = QTextBrowser(self)
        self.details.setOpenExternalLinks(False)
        recheck = QPushButton("Check again", self)
        recheck.clicked.connect(self.recheckRequested)
        sign = QPushButton("Sign…", self)
        sign.clicked.connect(self.signRequested)
        row = QHBoxLayout()
        row.addWidget(recheck)
        row.addStretch(1)
        row.addWidget(sign)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(self.summary)
        lay.addWidget(self.tree, 2)
        lay.addWidget(self.details, 3)
        lay.addLayout(row)
        self.results = []

    def clear(self, text: str = "No signatures in this document.") -> None:
        self.results = []
        self.tree.clear()
        self.details.clear()
        self.summary.setText(text)

    def set_busy(self, text: str) -> None:
        if not self.results:
            self.summary.setText(f"<i>{_html(text)}</i>")
        else:
            self.summary.setText(self._summary_html() + f"<br><i>{_html(text)}</i>")

    def set_results(self, results, eu_loaded: bool, eu_error: str | None = None) -> None:
        self.results = results
        self.eu_loaded = eu_loaded
        self.eu_error = eu_error
        self.tree.clear()
        if not results:
            self.clear()
            return
        for i, r in enumerate(results):
            label = r.signer or r.field_name
            item = QTreeWidgetItem([label, _short_verdict(r)])
            item.setIcon(0, _status_icon(verdict_kind(r.verdict)))
            item.setToolTip(0, r.verdict.value)
            item.setData(0, Qt.ItemDataRole.UserRole, i)
            self.tree.addTopLevelItem(item)
        text = self._summary_html()
        if not eu_loaded:
            text += ("<br><small>EU trusted lists not available"
                     + (f" ({_html(eu_error)})" if eu_error else "") + "; qualified status not checked.</small>")
        self.summary.setText(text)
        self.tree.setCurrentItem(self.tree.topLevelItem(0))

    def select(self, index: int) -> None:
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if item.data(0, Qt.ItemDataRole.UserRole) == index:
                self.tree.setCurrentItem(item)
                return

    def _summary_html(self) -> str:
        n = len(self.results)
        good = sum(1 for r in self.results if r.verdict.ok)
        bad = sum(1 for r in self.results if r.verdict.bad)
        if bad:
            color, text = "#d93025", f"{bad} of {n} signature(s) are NOT valid."
        elif good == n:
            q = sum(1 for r in self.results if r.qualified)
            color = "#1e8e3e"
            text = f"Signed and all {n} signature(s) are valid." if n > 1 else "Signed; the signature is valid."
            if q:
                text += f" {q} qualified (eIDAS)." if n > 1 else " Qualified electronic signature (eIDAS)."
        else:
            color, text = "#e37400", f"{n - good} of {n} signature(s) could not be fully verified."
        return f"<b style='color:{color}'>{_html(text)}</b>"

    def _selected(self, item, _prev) -> None:
        if item is None:
            self.details.clear()
            return
        r = self.results[item.data(0, Qt.ItemDataRole.UserRole)]
        self.details.setHtml(_details_html(r))

    def _activated(self, item, _col) -> None:
        index = item.data(0, Qt.ItemDataRole.UserRole)
        if self.results[index].page is not None:
            self.signatureActivated.emit(index)


def _short_verdict(r) -> str:
    from pdf45.signing.verify import Verdict

    return {
        Verdict.QUALIFIED: "Valid · qualified",
        Verdict.VALID: "Valid",
        Verdict.UNTRUSTED: "Signer not verified",
        Verdict.MODIFIED: "Changed after signing",
        Verdict.REVOKED: "Revoked",
        Verdict.INVALID: "Invalid",
        Verdict.ERROR: "Not checked",
    }[r.verdict]


def _fmt_time(t) -> str:
    return t.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z") if t else "—"


def _details_html(r) -> str:
    color = {"ok": "#1e8e3e", "warn": "#e37400", "bad": "#d93025"}[verdict_kind(r.verdict)]
    rows = [
        ("Signed by", r.signer),
        ("Certificate issuer", r.issuer),
        ("Signing time (signer's clock)", _fmt_time(r.signing_time)),
    ]
    if r.timestamp_time:
        rows.append(("Trusted timestamp", _fmt_time(r.timestamp_time)
                     + ("" if r.timestamp_ok else " (timestamp could not be verified)")))
    else:
        rows.append(("Trusted timestamp", "none"))
    if r.reason:
        rows.append(("Reason", r.reason))
    if r.location:
        rows.append(("Location", r.location))
    rows.append(("Covers", "the whole document" if r.covers_whole_document
                 else "the document as it was when signed (later revisions exist)"))
    rows.append(("Changes after signing", r.modifications or "—"))
    if r.qualified is None:
        q = "not checked" + (" (certificate claims to be qualified)" if r.claims_qualified else "")
    else:
        q = "yes – qualified electronic signature (eIDAS, EU trusted list)" if r.qualified else "no"
    rows.append(("Qualified", q))
    rows.append(("Long-term validation data (LTV)", "present" if r.ltv else "not present"))
    if r.page is not None:
        rows.append(("Location in document", f"page {r.page + 1} (double-click to go there)"))
    body = "".join(f"<tr><td style='padding-right:10px;color:palette(mid)'>{_html(k)}</td>"
                   f"<td>{_html(v)}</td></tr>" for k, v in rows)
    problems = "".join(f"<li>{_html(p)}</li>" for p in r.problems)
    tech = f"<pre style='font-size:small'>{_html(r.details)}</pre>" if r.details else ""
    return (f"<p style='color:{color}'><b>{_html(r.verdict.value)}</b></p>"
            f"<table>{body}</table>"
            + (f"<p><b>Problems</b></p><ul>{problems}</ul>" if problems else "")
            + (f"<p><b>Technical details</b></p>{tech}" if tech else ""))


# -------------------------------------------------------------- settings
class SettingsDialog(QDialog):
    def __init__(self, settings: SigningSettings, parent=None):
        super().__init__(parent)
        from pdf45.signing.sign import TSA_PRESETS

        self.setWindowTitle("Signature settings")
        self.setMinimumWidth(640)
        self.settings = settings

        # drivers
        self.drivers = QListWidget(self)
        self.extra = list(settings.extra_drivers)
        add = QPushButton("Add driver…", self)
        add.clicked.connect(self._add_driver)
        remove = QPushButton("Remove", self)
        remove.clicked.connect(self._remove_driver)
        row = QHBoxLayout()
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch(1)
        g_drv = QGroupBox("Token drivers (PKCS#11)", self)
        v = QVBoxLayout(g_drv)
        v.addWidget(QLabel("Detected automatically; add the driver of another token by hand "
                           "(a .dll on Windows, a .so on Linux)."))
        v.addWidget(self.drivers)
        v.addLayout(row)
        self._fill_drivers()

        # timestamp
        self.tsa = QComboBox(self)
        self.tsa.setEditable(True)
        for name, url in TSA_PRESETS:
            self.tsa.addItem(f"{url}", url)
            self.tsa.setItemData(self.tsa.count() - 1, name, Qt.ItemDataRole.ToolTipRole)
        self.tsa.setCurrentText(settings.tsa_url)
        self.tsa_user = QLineEdit(settings.tsa_user, self)
        self.tsa_user.setPlaceholderText("only for servers that need an account (e.g. certSIGN, DigiSign)")
        g_ts = QGroupBox("Timestamp server (TSA)", self)
        f = QFormLayout(g_ts)
        f.addRow("URL:", self.tsa)
        f.addRow("User:", self.tsa_user)
        f.addRow("", QLabel("<small>The password is asked when signing and never stored.</small>"))

        # validation
        self.online = QCheckBox("Check revocation online (OCSP / CRL)", self)
        self.online.setChecked(settings.online_checks)
        g_val = QGroupBox("Checking signatures", self)
        f = QFormLayout(g_val)
        f.addRow("", self.online)
        f.addRow("", QLabel("<small>Trust and qualified status come from the EU trusted lists "
                            "(all member states), refreshed every 7 days.</small>"))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        for w in (g_drv, g_ts, g_val, buttons):
            lay.addWidget(w)

    def _fill_drivers(self) -> None:
        self.drivers.clear()
        present = {d.path for d in installed_drivers()}
        for d in known_drivers():
            if d.path in present:
                self.drivers.addItem(QListWidgetItem(f"✓ {d.name} — {d.path}"))
        for p in self.extra:
            ok = Path(p).is_file()
            item = QListWidgetItem(f"{'✓' if ok else '✗'} (added) {p}")
            item.setData(Qt.ItemDataRole.UserRole, p)
            self.drivers.addItem(item)
        if self.drivers.count() == 0:
            self.drivers.addItem("No token driver found on this computer.")

    def _add_driver(self) -> None:
        filt = "PKCS#11 module (*.dll)" if sys.platform.startswith("win") else "PKCS#11 module (*.so *.so.*)"
        path, _ = QFileDialog.getOpenFileName(self, "PKCS#11 driver", "", filt + ";;All files (*)")
        if path and path not in self.extra:
            self.extra.append(path)
            self._fill_drivers()

    def _remove_driver(self) -> None:
        item = self.drivers.currentItem()
        p = item.data(Qt.ItemDataRole.UserRole) if item else None
        if p in self.extra:
            self.extra.remove(p)
            self._fill_drivers()

    def _save(self) -> None:
        s = self.settings
        s.extra_drivers = self.extra
        s.tsa_url = self.tsa.currentText().strip()
        s.tsa_user = self.tsa_user.text().strip()
        s.online_checks = self.online.isChecked()
        self.accept()
