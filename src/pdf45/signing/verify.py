# SPDX-License-Identifier: GPL-3.0-or-later
"""Validate the signatures embedded in a PDF."""

from __future__ import annotations

import asyncio
import enum
from dataclasses import dataclass, field
from datetime import datetime

from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign.diff_analysis import ModificationLevel
from pyhanko.sign.validation import async_validate_pdf_signature
from pyhanko.sign.validation.status import SignatureCoverageLevel

from pdf45.signing.token import cert_claims_qualified
from pdf45.signing.trust import TrustStore


class Verdict(enum.Enum):
    QUALIFIED = "Valid qualified electronic signature"
    VALID = "Valid signature"
    UNTRUSTED = "Signature intact, but the signer's identity could not be verified"
    MODIFIED = "Document was changed after signing"
    REVOKED = "Signer's certificate is revoked"
    INVALID = "Invalid signature"
    ERROR = "Signature could not be checked"

    @property
    def ok(self) -> bool:
        return self in (Verdict.QUALIFIED, Verdict.VALID)

    @property
    def bad(self) -> bool:
        return self in (Verdict.MODIFIED, Verdict.REVOKED, Verdict.INVALID)


@dataclass
class SignatureInfo:
    field_name: str
    verdict: Verdict
    signer: str = ""
    issuer: str = ""
    signing_time: datetime | None = None
    timestamp_time: datetime | None = None
    timestamp_ok: bool | None = None
    reason: str = ""
    location: str = ""
    page: int | None = None
    rect: tuple[float, float, float, float] | None = None  # visible widget, PDF user space
    covers_whole_document: bool = False
    modifications: str = ""
    ltv: bool = False
    qualified: bool | None = None  # None = not assessed (no EU trust list)
    claims_qualified: bool = False
    trusted: bool = False
    details: str = ""
    problems: list[str] = field(default_factory=list)


_MODS = {
    ModificationLevel.NONE: "none",
    ModificationLevel.LTA_UPDATES: "only validation data / document timestamps added",
    ModificationLevel.FORM_FILLING: "form filling and later signatures",
    ModificationLevel.ANNOTATIONS: "annotations, form filling and later signatures",
    ModificationLevel.OTHER: "other changes",
}


def _page_of_field(reader: PdfFileReader, sig_field) -> int | None:
    try:
        page_ref = sig_field.get("/P")
        if page_ref is None:
            kids = sig_field.get("/Kids")
            if kids:
                page_ref = kids[0].get_object().get("/P")
        if page_ref is None:
            return None
        target = page_ref.reference if hasattr(page_ref, "reference") else page_ref
        n = int(reader.root["/Pages"]["/Count"])
        for i in range(n):
            ref, _ = reader.find_page_for_modification(i)
            if ref == target or getattr(ref, "idnum", None) == getattr(target, "idnum", -1):
                return i
    except Exception:
        return None
    return None


def _widget_rect(sig_field) -> tuple[float, float, float, float] | None:
    """Rectangle of a visible signature widget, or None for invisible signatures."""
    try:
        widget = sig_field
        if "/Rect" not in widget and widget.get("/Kids"):
            widget = widget["/Kids"][0].get_object()
        x1, y1, x2, y2 = (float(v) for v in widget["/Rect"])
        if int(widget.get("/F", 0)) & 2:  # hidden
            return None
    except Exception:
        return None
    rect = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
    return rect if rect[2] - rect[0] > 1 and rect[3] - rect[1] > 1 else None


def _has_dss(reader: PdfFileReader) -> bool:
    try:
        return "/DSS" in reader.root
    except Exception:
        return False


async def _validate_one(reader, sig, trust: TrustStore, use_eu: bool, online: bool) -> SignatureInfo:
    vc = trust.validation_context(use_eu=use_eu, allow_fetching=online, revocation_mode="soft-fail",
                                  other_certs=list(sig.other_embedded_certs))
    ts_vc = trust.validation_context(use_eu=use_eu, allow_fetching=online, revocation_mode="soft-fail")
    sig_dict = sig.sig_object
    info = SignatureInfo(field_name=sig.field_name, verdict=Verdict.ERROR)
    info.reason = str(sig_dict.get("/Reason", "") or "")
    info.location = str(sig_dict.get("/Location", "") or "")
    info.page = _page_of_field(reader, sig.sig_field)
    info.rect = _widget_rect(sig.sig_field)
    info.ltv = _has_dss(reader)
    try:
        cert = sig.signer_cert
        info.signer = cert.subject.native.get("common_name") or cert.subject.human_friendly
        info.issuer = cert.issuer.native.get("common_name") or cert.issuer.human_friendly
        info.claims_qualified = cert_claims_qualified(cert)
    except Exception:
        pass
    try:
        status = await async_validate_pdf_signature(sig, signer_validation_context=vc,
                                                     ts_validation_context=ts_vc)
    except Exception as e:
        info.problems.append(f"{e.__class__.__name__}: {e}")
        info.details = "\n".join(info.problems)
        return info

    info.signing_time = status.signer_reported_dt
    if status.timestamp_validity is not None:
        info.timestamp_time = status.timestamp_validity.timestamp
        info.timestamp_ok = bool(status.timestamp_validity.intact and status.timestamp_validity.valid)
    info.covers_whole_document = status.coverage == SignatureCoverageLevel.ENTIRE_FILE
    mod = status.modification_level
    info.modifications = _MODS.get(mod, "unknown") if mod is not None else "unknown"
    info.trusted = bool(status.trusted)

    if use_eu and status.validation_path is not None:
        registry = trust.eu_registry()
        if registry is not None:
            try:
                from pyhanko.sign.validation.qualified.assess import QualificationAssessor

                moment = info.timestamp_time or info.signing_time
                q = QualificationAssessor(registry).check_entity_cert_qualified(
                    status.validation_path, moment)
                info.qualified = bool(q.status.qualified)
            except Exception as e:
                info.problems.append(f"Qualification check failed: {e}")

    if not status.intact or not status.valid:
        info.verdict = Verdict.INVALID
    elif status.revoked:
        info.verdict = Verdict.REVOKED
    elif (status.coverage not in (SignatureCoverageLevel.ENTIRE_FILE, SignatureCoverageLevel.ENTIRE_REVISION)
          or not status.docmdp_ok or (mod is not None and mod == ModificationLevel.OTHER)):
        info.verdict = Verdict.MODIFIED
    elif not status.trusted:
        info.verdict = Verdict.UNTRUSTED
    elif info.qualified:
        info.verdict = Verdict.QUALIFIED
    else:
        info.verdict = Verdict.VALID

    try:
        info.details = status.pretty_print_details()
    except Exception:
        info.details = status.summary()
    return info


def verify_document(path: str, trust: TrustStore, *, use_eu: bool = True, online: bool = True,
                    password: str | None = None) -> list[SignatureInfo]:
    """Validate every (non-timestamp) signature of ``path``. Blocking: call from a worker thread."""
    if use_eu:
        trust.eu_registry()  # download/parse outside the event loop below

    async def run():
        with open(path, "rb") as f:
            reader = PdfFileReader(f, strict=False)
            if reader.encrypted:
                if not password:
                    return []
                reader.decrypt(password)
            sigs = reader.embedded_regular_signatures
            return [await _validate_one(reader, s, trust, use_eu, online) for s in sigs]

    return asyncio.run(run())


def count_signatures(path: str, password: str | None = None) -> int:
    """Quick check (no validation) used to decide whether to show the signatures panel."""
    try:
        with open(path, "rb") as f:
            reader = PdfFileReader(f, strict=False)
            if reader.encrypted:
                if not password:
                    return 0
                reader.decrypt(password)
            return len(reader.embedded_regular_signatures)
    except Exception:
        return 0
