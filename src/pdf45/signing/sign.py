# SPDX-License-Identifier: GPL-3.0-or-later
"""Create PAdES signatures with a key that never leaves the hardware token.

Levels produced (ETSI EN 319 142-1):

* B-B  - signature only;
* B-T  - plus an RFC 3161 timestamp from a timestamping authority;
* B-LT - plus the certificate chain and revocation data (OCSP/CRL) stored
  in the document (DSS), so it can be validated offline for years;
* B-LTA - B-LT plus a document timestamp protecting that data.
"""

from __future__ import annotations

import io
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.sign import fields, signers
from pyhanko.sign.pkcs11 import PKCS11Signer
from pyhanko.sign.timestamps import HTTPTimeStamper

from pdf45.signing.token import TokenCert, TokenError, open_session
from pdf45.signing.trust import TrustStore

TSA_PRESETS = [
    ("Sectigo - eIDAS qualified (free)", "https://timestamp.sectigo.com/qualified"),
    ("DigiCert (free)", "http://timestamp.digicert.com"),
]
DEFAULT_TSA = TSA_PRESETS[0][1]

APPEARANCE_TEXT = {
    "en": "Digitally signed by:\n%(signer)s\nDate: %(ts)s%(extra)s",
    "ro": "Semnat digital de:\n%(signer)s\nData: %(ts)s%(extra)s",
}
APPEARANCE_LABELS = {
    "en": {"reason": "Reason", "location": "Location"},
    "ro": {"reason": "Motiv", "location": "Locație"},
}


class SigningError(Exception):
    """A user-presentable signing failure."""


@dataclass
class SignRequest:
    input_path: str
    output_path: str
    token_cert: TokenCert
    pin: str | None
    reason: str = ""
    location: str = ""
    contact: str = ""
    # visible signature: page index and box (x1, y1, x2, y2) in PDF user space
    visible: bool = False
    page: int = 0
    box: tuple[float, float, float, float] | None = None
    existing_field: str | None = None
    timestamp_url: str | None = DEFAULT_TSA
    tsa_user: str = ""
    tsa_password: str = ""
    ltv: bool = True
    appearance_lang: str = "en"
    font_path: str | None = None
    logo_path: str | None = None
    pdf_password: str | None = None


def existing_signature_fields(path: str, password: str | None = None) -> tuple[list[str], list[str]]:
    """Return (empty field names, all field names) of the PDF's signature fields."""
    from pyhanko.pdf_utils.reader import PdfFileReader

    with open(path, "rb") as f:
        r = PdfFileReader(f, strict=False)
        if r.encrypted and password:
            r.decrypt(password)
        empty, every = [], []
        for name, value, _ref in fields.enumerate_sig_fields(r):
            every.append(name)
            if value is None:
                empty.append(name)
    return empty, every


def _new_field_name(taken: list[str]) -> str:
    n = 1
    while f"Signature{n}" in taken:
        n += 1
    return f"Signature{n}"


def _patch_font_units() -> None:
    """Make pyHanko's embedded OpenType fonts use PDF glyph-space units.

    pyHanko (0.37) writes glyph widths (/W) and TJ kerning adjustments in the
    font's own units, but PDF wants thousandths of an em.  For fonts with
    2048 units per em (DejaVu, Arial, ...) every viewer then spaces the
    letters about twice too wide.  Asking HarfBuzz for positions in 1000
    units per em and scaling the widths the same way fixes both.
    """
    from pyhanko.pdf_utils.font.opentype import GlyphAccumulator

    init = GlyphAccumulator.__init__
    if getattr(init, "_pdf45_patched", False):
        return
    get_cid_and_width = GlyphAccumulator._get_cid_and_width

    def __init__(self, *args, **kwargs):
        init(self, *args, **kwargs)
        if self.units_per_em != 1000:
            self._pdf45_width_scale = 1000 / self.units_per_em
            self.hb_font.scale = (1000, 1000)
            self.units_per_em = 1000

    def _get_cid_and_width(self, glyph_id):
        known = glyph_id in self._glyphs
        cid, width = get_cid_and_width(self, glyph_id)
        scale = getattr(self, "_pdf45_width_scale", None)
        if scale and not known:
            width = round(width * scale)
            self._glyphs[glyph_id] = (cid, width)
        return cid, width

    __init__._pdf45_patched = True
    GlyphAccumulator.__init__ = __init__
    GlyphAccumulator._get_cid_and_width = _get_cid_and_width


def _stamp_style(req: SignRequest):
    from pyhanko.pdf_utils.layout import AxisAlignment, InnerScaling, Margins, SimpleBoxLayoutRule
    from pyhanko.pdf_utils.text import TextBoxStyle
    from pyhanko.stamp import TextStampStyle

    font = None
    if req.font_path and Path(req.font_path).is_file():
        from pyhanko.pdf_utils.font.opentype import GlyphAccumulatorFactory

        _patch_font_units()
        font = GlyphAccumulatorFactory(req.font_path)
    box_style = TextBoxStyle(font=font, font_size=10) if font else TextBoxStyle(font_size=10)
    background = None
    if req.logo_path and Path(req.logo_path).is_file():
        from pyhanko.pdf_utils.images import PdfImage

        background = PdfImage(req.logo_path)
    return TextStampStyle(
        stamp_text=APPEARANCE_TEXT.get(req.appearance_lang, APPEARANCE_TEXT["en"]),
        text_box_style=box_style,
        border_width=1,
        timestamp_format="%Y-%m-%d %H:%M:%S %z",
        # Faded logo behind the text, like Adobe's watermark.
        background=background,
        background_opacity=0.18,
        background_layout=SimpleBoxLayoutRule(
            x_align=AxisAlignment.ALIGN_MID, y_align=AxisAlignment.ALIGN_MID,
            margins=Margins.uniform(4), inner_content_scaling=InnerScaling.SHRINK_TO_FIT),
        # Shrink the text so that it always fits the rectangle the user drew.
        inner_content_layout=SimpleBoxLayoutRule(
            x_align=AxisAlignment.ALIGN_MIN, y_align=AxisAlignment.ALIGN_MID,
            margins=Margins.uniform(5), inner_content_scaling=InnerScaling.SHRINK_TO_FIT),
    )


def _extra_text(req: SignRequest) -> str:
    labels = APPEARANCE_LABELS.get(req.appearance_lang, APPEARANCE_LABELS["en"])
    extra = ""
    if req.reason:
        extra += f"\n{labels['reason']}: {req.reason}"
    if req.location:
        extra += f"\n{labels['location']}: {req.location}"
    return extra


def sign_document(req: SignRequest, trust: TrustStore,
                  progress: Callable[[str], None] = lambda _m: None) -> str:
    """Sign ``req.input_path`` into ``req.output_path``. Returns a short description of the level."""
    tc = req.token_cert
    if not tc.is_valid_now:
        raise SigningError(f"The certificate is not valid now (valid {tc.not_before:%Y-%m-%d} - "
                           f"{tc.not_after:%Y-%m-%d}).")

    timestamper = None
    if req.timestamp_url:
        auth = (req.tsa_user, req.tsa_password) if req.tsa_user else None
        timestamper = _ReportingTimeStamper(req.timestamp_url, progress, req.ltv, timeout=30, auth=auth)

    vc = None
    if req.ltv:
        progress("Loading EU trusted lists…")
        vc = trust.validation_context(use_eu=True, revocation_mode="hard-fail",
                                      other_certs=tc.other_certs)

    empty_fields, all_fields = existing_signature_fields(req.input_path, req.pdf_password)
    field_name = req.existing_field or _new_field_name(all_fields)
    new_field = None
    if not req.existing_field:
        if req.visible and req.box:
            x1, y1, x2, y2 = req.box
            new_field = fields.SigFieldSpec(field_name, on_page=req.page,
                                            box=(int(x1), int(y1), int(x2), int(y2)))
        else:
            new_field = fields.SigFieldSpec(field_name, on_page=req.page)

    meta = signers.PdfSignatureMetadata(
        field_name=field_name,
        md_algorithm="sha256",
        reason=req.reason or None,
        location=req.location or None,
        contact_info=req.contact or None,
        subfilter=fields.SigSeedSubFilter.PADES,
        embed_validation_info=req.ltv,
        use_pades_lta=bool(req.ltv and timestamper),
        validation_context=vc,
    )

    progress("Logging in to the token…")
    session = open_session(tc, req.pin)
    try:
        signer = PKCS11Signer(
            session,
            signing_cert=tc.cert,
            ca_chain=tc.other_certs,
            cert_id=tc.cert_id,
            key_id=tc.cert_id,
            key_label=None if tc.cert_id else tc.cert_label,
            cert_label=None if tc.cert_id else tc.cert_label,
        )
        visible = req.visible and (req.box is not None or req.existing_field in empty_fields)
        pdf_signer = signers.PdfSigner(
            meta,
            signer,
            timestamper=timestamper,
            stamp_style=_stamp_style(req) if visible else None,
            new_field_spec=new_field,
        )
        progress("Signing with the token…")
        out = io.BytesIO()
        with open(req.input_path, "rb") as inf:
            writer = IncrementalPdfFileWriter(inf, strict=False)
            if writer.prev.encrypted:
                if not req.pdf_password:
                    raise SigningError("The document is password-protected.")
                writer.encrypt(req.pdf_password)
            try:
                pdf_signer.sign_pdf(writer, output=out,
                                    appearance_text_params={"extra": _extra_text(req)})
            except TokenError:
                raise
            except Exception as e:
                raise SigningError(_explain(e, req)) from e
    finally:
        try:
            session.close()
        except Exception:
            pass

    progress("Saving…")
    _atomic_write(req.output_path, out.getvalue())
    level = "PAdES B-B"
    if timestamper:
        level = "PAdES B-LTA" if req.ltv else "PAdES B-T"
    elif req.ltv:
        level = "PAdES B-LT"
    return level


class _ReportingTimeStamper(HTTPTimeStamper):
    """Reports which of the (up to two) timestamp requests is running.

    PAdES B-LTA asks the server twice: once for the signature, once for the
    document timestamp that seals the long-term validation data.
    """

    def __init__(self, url, progress, ltv: bool, **kw):
        super().__init__(url, **kw)
        self._progress = progress
        self._ltv = ltv
        self._calls = 0

    async def async_timestamp(self, message_digest, md_algorithm):
        self._calls += 1
        first = self._calls == 1
        self._progress("Getting the trusted timestamp…" if first
                       else "Sealing the validation data with a document timestamp…")
        token = await super().async_timestamp(message_digest, md_algorithm)
        if first and self._ltv:
            self._progress("Collecting certificate chains and revocation data (LTV)…")
        return token


def _explain(e: Exception, req: SignRequest) -> str:
    name = e.__class__.__name__
    text = str(e) or name
    if "Timestamp" in name or "timestamp" in text.lower():
        return (f"The timestamp server did not answer ({req.timestamp_url}).\n"
                f"Check the internet connection or choose another server in the settings.\n\n{text}")
    if req.ltv and ("revocation" in text.lower() or "path" in text.lower() or "Revinfo" in name
                    or "PathBuilding" in name or "PathValidation" in name):
        return ("Could not collect the certificate chain / revocation data for long-term validation "
                "(LTV). Check the internet connection, or sign without LTV.\n\n" + text)
    if "PKCS11" in name or "pkcs11" in type(e).__module__:
        return f"The token refused the operation: {text}"
    return f"{name}: {text}"


def _atomic_write(path: str, data: bytes) -> None:
    target = Path(path)
    fd, tmp = tempfile.mkstemp(prefix=".pdf45-", suffix=".pdf", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def default_output_path(input_path: str) -> str:
    p = Path(input_path)
    stem = p.stem
    candidate = p.with_name(f"{stem}-signed.pdf")
    n = 2
    while candidate.exists():
        candidate = p.with_name(f"{stem}-signed-{n}.pdf")
        n += 1
    return str(candidate)


# ------------------------------------------------------------- page geometry
@dataclass(frozen=True)
class PageGeometry:
    """Visible area of a page in PDF user space, plus its /Rotate value."""

    llx: float
    lly: float
    urx: float
    ury: float
    rotate: int = 0

    @property
    def display_size(self) -> tuple[float, float]:
        """Width and height (points) as the page is shown on screen."""
        w, h = self.urx - self.llx, self.ury - self.lly
        return (h, w) if self.rotate in (90, 270) else (w, h)

    def to_user(self, dx: float, dy: float) -> tuple[float, float]:
        """Map a point given in displayed points (origin top-left) to PDF user space."""
        if self.rotate == 90:
            return self.llx + dy, self.lly + dx
        if self.rotate == 180:
            return self.urx - dx, self.lly + dy
        if self.rotate == 270:
            return self.urx - dy, self.ury - dx
        return self.llx + dx, self.ury - dy

    def to_display(self, x: float, y: float) -> tuple[float, float]:
        """Inverse of :meth:`to_user`: PDF user space -> displayed points (origin top-left)."""
        if self.rotate == 90:
            return y - self.lly, x - self.llx
        if self.rotate == 180:
            return self.urx - x, y - self.lly
        if self.rotate == 270:
            return self.ury - y, self.urx - x
        return x - self.llx, self.ury - y

    def box_from_display(self, x1: float, y1: float, x2: float, y2: float) -> tuple[float, float, float, float]:
        ax, ay = self.to_user(x1, y1)
        bx, by = self.to_user(x2, y2)
        return min(ax, bx), min(ay, by), max(ax, bx), max(ay, by)


def _inherited(page, key):
    node = page
    while node is not None:
        if key in node:
            return node[key]
        parent = node.get("/Parent")
        node = parent.get_object() if parent is not None else None
    return None


def page_geometry(path: str, page_index: int, password: str | None = None) -> PageGeometry:
    from pyhanko.pdf_utils.reader import PdfFileReader

    with open(path, "rb") as f:
        r = PdfFileReader(f, strict=False)
        if r.encrypted and password:
            r.decrypt(password)
        ref, _ = r.find_page_for_modification(page_index)
        page = ref.get_object()
        box = _inherited(page, "/CropBox") or _inherited(page, "/MediaBox") or [0, 0, 612, 792]
        llx, lly, urx, ury = (float(v) for v in box)
        rot = int(_inherited(page, "/Rotate") or 0) % 360
    return PageGeometry(min(llx, urx), min(lly, ury), max(llx, urx), max(lly, ury), rot)
