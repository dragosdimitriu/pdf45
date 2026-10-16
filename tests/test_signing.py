# SPDX-License-Identifier: GPL-3.0-or-later
"""Signing and validation tests.

The PKCS#11 tests need SoftHSM2 (a software token): ``apt install softhsm2``
on Linux. They are skipped when it is not installed. Everything runs
offline (no timestamp server, no LTV, no EU trusted lists).
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("pyhanko")

from pdf45.signing.drivers import Driver, known_drivers  # noqa: E402
from pdf45.signing.sign import PageGeometry, SignRequest, page_geometry, sign_document  # noqa: E402
from pdf45.signing.trust import TrustStore  # noqa: E402
from pdf45.signing.verify import Verdict, verify_document  # noqa: E402

from pki import make_pki  # noqa: E402

SAMPLE = Path(__file__).parent / "fixtures" / "sample.pdf"
FONT = Path(__file__).parent.parent / "src" / "pdf45" / "resources" / "fonts" / "DejaVuSans.ttf"
PIN = "1234"


# ----------------------------------------------------------- geometry
def test_page_geometry_of_sample():
    g = page_geometry(str(SAMPLE), 0)
    assert g.rotate == 0
    w, h = g.display_size
    assert w == pytest.approx(595.28, abs=0.1) and h == pytest.approx(841.89, abs=0.1)
    # top-left of the screen is the top-left of the page in PDF space
    assert g.to_user(0, 0) == (g.llx, g.ury)


@pytest.mark.parametrize("rot, corner, expected", [
    (0, (0, 0), (0, 800)),      # screen top-left -> page top-left
    (90, (0, 0), (0, 0)),       # page turned clockwise: its bottom-left is now top-left
    (180, (0, 0), (600, 0)),
    (270, (0, 0), (600, 800)),
])
def test_rotated_pages_map_to_user_space(rot, corner, expected):
    g = PageGeometry(0, 0, 600, 800, rot)
    assert g.to_user(*corner) == expected
    w, h = g.display_size
    assert (w, h) == ((800, 600) if rot in (90, 270) else (600, 800))


# ------------------------------------------------- validation (no token)
def _simple_sign(pki, src: Path, dst: Path) -> None:
    from asn1crypto import keys
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.sign import fields, signers
    from pyhanko_certvalidator.registry import SimpleCertificateStore

    signer = signers.SimpleSigner(
        signing_cert=pki.asn1(pki.signer_cert),
        signing_key=keys.PrivateKeyInfo.wrap(keys.RSAPrivateKey.load(pki.signer_der_key), "rsa"),
        cert_registry=SimpleCertificateStore.from_certs([pki.asn1(pki.ca_cert)]),
    )
    with open(src, "rb") as f:
        out = io.BytesIO()
        signers.PdfSigner(
            signers.PdfSignatureMetadata(field_name="Sig1", subfilter=fields.SigSeedSubFilter.PADES),
            signer,
        ).sign_pdf(IncrementalPdfFileWriter(f), output=out)
    dst.write_bytes(out.getvalue())


def test_validation_trusted_untrusted_and_tampered(tmp_path):
    pki = make_pki()
    signed = tmp_path / "signed.pdf"
    _simple_sign(pki, SAMPLE, signed)

    trusted = TrustStore(None, extra_roots=[pki.asn1(pki.ca_cert)])
    [info] = verify_document(str(signed), trusted, use_eu=False, online=False)
    assert info.verdict == Verdict.VALID
    assert info.signer == "Ion Popescu-Știrbu"
    assert info.covers_whole_document

    [info] = verify_document(str(signed), TrustStore(None), use_eu=False, online=False)
    assert info.verdict == Verdict.UNTRUSTED

    data = bytearray(signed.read_bytes())
    i = data.find(b"/Producer")
    data[i + 1] = ord("Q")  # change a byte covered by the signature
    tampered = tmp_path / "tampered.pdf"
    tampered.write_bytes(bytes(data))
    [info] = verify_document(str(tampered), trusted, use_eu=False, online=False)
    assert info.verdict == Verdict.INVALID


def test_unsigned_document_has_no_signatures():
    assert verify_document(str(SAMPLE), TrustStore(None), use_eu=False, online=False) == []


# ---------------------------------------------------- PKCS#11 (SoftHSM2)
def _softhsm_lib() -> str | None:
    env = os.environ.get("PDF45_TEST_PKCS11")
    if env and Path(env).is_file():
        return env
    for d in known_drivers():
        if "SoftHSM" in d.name and Path(d.path).is_file():
            return d.path
    return None


@pytest.fixture
def softhsm_token(tmp_path, monkeypatch):
    lib = _softhsm_lib()
    util = shutil.which("softhsm2-util")
    if not lib or not util:
        pytest.skip("SoftHSM2 not installed")
    tokens = tmp_path / "tokens"
    tokens.mkdir()
    conf = tmp_path / "softhsm2.conf"
    conf.write_text(f"directories.tokendir = {tokens}\nobjectstore.backend = file\nlog.level = ERROR\n")
    monkeypatch.setenv("SOFTHSM2_CONF", str(conf))
    subprocess.run([util, "--init-token", "--free", "--label", "pdf45-test", "--pin", PIN, "--so-pin", "0000"],
                   check=True, capture_output=True)

    import pkcs11
    from cryptography.hazmat.primitives import serialization
    from pkcs11 import Attribute, ObjectClass
    from pkcs11.util.rsa import decode_rsa_private_key
    from pkcs11.util.x509 import decode_x509_certificate

    pki = make_pki()
    lib_obj = pkcs11.lib(lib)
    lib_obj.reinitialize()  # pick up the token created by softhsm2-util
    token = lib_obj.get_token(token_label="pdf45-test")
    key_id = b"\x01\x02"
    with token.open(rw=True, user_pin=PIN) as s:
        attrs = decode_rsa_private_key(pki.signer_der_key)
        attrs.update({Attribute.TOKEN: True, Attribute.ID: key_id, Attribute.LABEL: "signer",
                      Attribute.SIGN: True, Attribute.PRIVATE: True, Attribute.SENSITIVE: True})
        s.create_object(attrs)
        for cert, label, cid in ((pki.signer_cert, "signer", key_id), (pki.ca_cert, "ca", b"\x09")):
            ca = decode_x509_certificate(cert.public_bytes(serialization.Encoding.DER))
            ca.update({Attribute.TOKEN: True, Attribute.ID: cid, Attribute.LABEL: label,
                       Attribute.CLASS: ObjectClass.CERTIFICATE})
            s.create_object(ca)
    return Driver("SoftHSM2 (testing)", lib), pki


def _request(tc, src, dst, **kw) -> SignRequest:
    return SignRequest(input_path=str(src), output_path=str(dst), token_cert=tc, pin=PIN,
                       timestamp_url=None, ltv=False, font_path=str(FONT), **kw)


def test_token_lists_only_the_signing_certificate(softhsm_token):
    from pdf45.signing.token import list_signing_certificates

    driver, pki = softhsm_token
    certs, problems = list_signing_certificates([driver])
    assert problems == []
    assert [c.subject_cn for c in certs] == ["Ion Popescu-Știrbu"]
    assert certs[0].token_label == "pdf45-test"
    assert len(certs[0].other_certs) == 1  # the CA, used for the chain


@pytest.mark.parametrize("visible", [False, True])
def test_sign_with_token_and_validate(softhsm_token, tmp_path, visible):
    from pdf45.signing.token import list_signing_certificates

    driver, pki = softhsm_token
    [tc], _ = list_signing_certificates([driver])
    out = tmp_path / "signed.pdf"
    level = sign_document(_request(tc, SAMPLE, out, reason="Aprobat", location="București",
                                   visible=visible, page=1, box=(300, 40, 520, 110) if visible else None,
                                   appearance_lang="ro"),
                          TrustStore(None))
    assert level == "PAdES B-B"

    trusted = TrustStore(None, extra_roots=[pki.asn1(pki.ca_cert)])
    [info] = verify_document(str(out), trusted, use_eu=False, online=False)
    assert info.verdict == Verdict.VALID
    assert info.reason == "Aprobat" and info.location == "București"
    if visible:
        assert info.page == 1

    # A second signature on top of the first keeps both valid.
    out2 = tmp_path / "signed2.pdf"
    sign_document(_request(tc, out, out2), TrustStore(None))
    infos = verify_document(str(out2), trusted, use_eu=False, online=False)
    assert [i.verdict for i in infos] == [Verdict.VALID, Verdict.VALID]
    assert infos[0].covers_whole_document is False and infos[1].covers_whole_document


def test_wrong_pin_is_reported(softhsm_token, tmp_path):
    from pdf45.signing.token import TokenError, list_signing_certificates

    driver, _ = softhsm_token
    [tc], _ = list_signing_certificates([driver])
    req = _request(tc, SAMPLE, tmp_path / "x.pdf")
    req.pin = "9999"
    with pytest.raises(TokenError, match="Wrong PIN"):
        sign_document(req, TrustStore(None))
    assert not (tmp_path / "x.pdf").exists()


# ------------------------------------------------- visible appearance
def test_visible_appearance_fits_and_uses_pdf_units(tmp_path):
    """The stamp text is laid out in PDF glyph units and rendered inside its box."""
    pypdfium2 = pytest.importorskip("pypdfium2")
    from asn1crypto import keys
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.sign import fields, signers
    from pyhanko_certvalidator.registry import SimpleCertificateStore

    from pdf45.signing.sign import _extra_text, _stamp_style

    pki = make_pki("DIMITRIU DRAGOS-ȘTEFAN")
    signer = signers.SimpleSigner(
        signing_cert=pki.asn1(pki.signer_cert),
        signing_key=keys.PrivateKeyInfo.wrap(keys.RSAPrivateKey.load(pki.signer_der_key), "rsa"),
        cert_registry=SimpleCertificateStore.from_certs([pki.asn1(pki.ca_cert)]),
    )
    logo = Path(__file__).parent.parent / "src" / "pdf45" / "resources" / "pdf45.png"
    req = SignRequest("", "", None, None, reason="Aprobat", location="Iași", appearance_lang="ro",
                      font_path=str(FONT), logo_path=str(logo))
    box = (359, 36, 559, 106)
    out = io.BytesIO()
    with open(SAMPLE, "rb") as f:
        signers.PdfSigner(signers.PdfSignatureMetadata(field_name="S"), signer, stamp_style=_stamp_style(req),
                          new_field_spec=fields.SigFieldSpec("S", on_page=0, box=box)
                          ).sign_pdf(IncrementalPdfFileWriter(f), output=out,
                                     appearance_text_params={"extra": _extra_text(req)})

    # Glyph widths are thousandths of an em (DejaVu's own unit is 2048).
    reader = PdfFileReader(io.BytesIO(out.getvalue()))
    ap = reader.embedded_regular_signatures[0].sig_field["/AP"]["/N"].get_object()

    def fonts(xobj):
        res = xobj.get("/Resources", {})
        for v in res.get("/Font", {}).values():
            yield v.get_object()
        for v in res.get("/XObject", {}).values():
            yield from fonts(v.get_object())

    widths = [int(w) for f in fonts(ap)
              for item in f["/DescendantFonts"][0].get_object()["/W"] if not isinstance(item, int)
              for w in item]
    assert widths and max(widths) <= 1100

    # PDFium (with its form environment) draws ink inside the box and nothing right of it.
    pdf = pypdfium2.PdfDocument(out.getvalue())
    pdf.init_forms()
    img = pdf[0].render(scale=2, may_draw_forms=True).to_pil().convert("L")
    x1, y1, x2, y2 = box
    h = img.height // 2
    inside = img.crop((2 * x1 + 6, 2 * (h - y2) + 6, 2 * x2 - 6, 2 * (h - y1) - 6))
    right = img.crop((2 * x2 + 4, 2 * (h - y2), min(img.width, 2 * x2 + 60), 2 * (h - y1)))
    assert inside.getextrema()[0] < 100
    assert right.getextrema()[0] > 240
