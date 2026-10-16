# SPDX-License-Identifier: GPL-3.0-or-later
"""Enumerate hardware tokens and the signing certificates stored on them.

Certificates are public objects, so they are listed without logging in;
the PIN is only needed for the signature itself (see sign.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import pkcs11
from asn1crypto import x509
from pkcs11 import Attribute, ObjectClass, TokenFlag

from pdf45.signing.drivers import Driver

# id-etsi-qcs-QcCompliance: the certificate claims to be an EU qualified certificate.
QC_COMPLIANCE_OID = "0.4.0.1862.1.1"


class TokenError(Exception):
    """A user-presentable error while talking to a token."""


@dataclass
class TokenCert:
    driver: Driver
    slot_id: int
    token_label: str
    token_model: str
    token_serial: str
    token_flags: int
    cert: x509.Certificate
    cert_id: bytes | None
    cert_label: str | None
    other_certs: list[x509.Certificate] = field(default_factory=list)

    # ------------------------------------------------------------- display
    @property
    def subject_cn(self) -> str:
        return self.cert.subject.native.get("common_name") or self.cert.subject.human_friendly

    @property
    def issuer_cn(self) -> str:
        return self.cert.issuer.native.get("common_name") or self.cert.issuer.human_friendly

    @property
    def not_after(self) -> datetime:
        return self.cert["tbs_certificate"]["validity"]["not_after"].native

    @property
    def not_before(self) -> datetime:
        return self.cert["tbs_certificate"]["validity"]["not_before"].native

    @property
    def is_valid_now(self) -> bool:
        now = datetime.now(timezone.utc)
        return self.not_before <= now <= self.not_after

    @property
    def is_qualified(self) -> bool:
        return cert_claims_qualified(self.cert)

    @property
    def protected_auth(self) -> bool:
        """PIN is entered on the device (PIN pad) rather than in the application."""
        return bool(self.token_flags & TokenFlag.PROTECTED_AUTHENTICATION_PATH)

    def pin_warning(self) -> str | None:
        f = self.token_flags
        if f & TokenFlag.USER_PIN_LOCKED:
            return "The token PIN is locked. Unlock it with the vendor's token tool."
        if f & TokenFlag.USER_PIN_FINAL_TRY:
            return "Warning: one wrong PIN left - another mistake locks the token."
        if f & TokenFlag.USER_PIN_COUNT_LOW:
            return "Warning: a wrong PIN was entered recently; few attempts remain."
        return None

    def describe(self) -> str:
        q = " [qualified]" if self.is_qualified else ""
        return f"{self.subject_cn} - {self.issuer_cn}, valid until {self.not_after:%Y-%m-%d}{q}"


def cert_claims_qualified(cert: x509.Certificate) -> bool:
    for ext in cert["tbs_certificate"]["extensions"]:
        if ext["extn_id"].dotted == "1.3.6.1.5.5.7.1.3":  # qcStatements
            try:
                for st in ext["extn_value"].parsed:
                    if st["statement_id"].dotted == QC_COMPLIANCE_OID:
                        return True
            except Exception:  # unparseable extension: be conservative
                return False
    return False


def _is_signing_cert(cert: x509.Certificate) -> bool:
    bc = cert.basic_constraints_value
    if bc is not None and bc["ca"].native:
        return False
    ku = cert.key_usage_value
    if ku is None:
        return True
    usages = set(ku.native)
    return bool(usages & {"digital_signature", "non_repudiation"})


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("ascii", "replace")
    return str(value or "").strip()


def _token_certs(driver: Driver, slot) -> list[TokenCert]:
    token = slot.get_token()
    found: list[TokenCert] = []
    if not token.flags & TokenFlag.TOKEN_INITIALIZED:
        return found
    with token.open() as session:
        objs = list(session.get_objects({Attribute.CLASS: ObjectClass.CERTIFICATE}))
        certs = []
        for o in objs:
            try:
                cert = x509.Certificate.load(o[Attribute.VALUE])
            except Exception:
                continue
            try:
                cid = o[Attribute.ID] or None
            except pkcs11.PKCS11Error:
                cid = None
            try:
                label = o[Attribute.LABEL] or None
            except pkcs11.PKCS11Error:
                label = None
            certs.append((cert, cid, label))
    all_certs = [c for c, _, _ in certs]
    for cert, cid, label in certs:
        if not _is_signing_cert(cert):
            continue
        found.append(
            TokenCert(
                driver=driver,
                slot_id=slot.slot_id,
                token_label=(token.label or "").strip(),
                token_model=(token.model or "").strip(),
                token_serial=_text(getattr(token, "serial", "")),
                token_flags=int(token.flags),
                cert=cert,
                cert_id=cid,
                cert_label=label,
                other_certs=[c for c in all_certs if c is not cert],
            )
        )
    return found


def list_signing_certificates(drivers: list[Driver]) -> tuple[list[TokenCert], list[str]]:
    """Return (certificates, problems) across all tokens reachable through ``drivers``."""
    result: list[TokenCert] = []
    problems: list[str] = []
    for drv in drivers:
        try:
            lib = pkcs11.lib(drv.path)
            slots = lib.get_slots(token_present=True)
        except Exception as e:  # driver failed to load or initialise
            problems.append(f"{drv.name}: {e.__class__.__name__} {e}".strip())
            continue
        for slot in slots:
            try:
                result.extend(_token_certs(drv, slot))
            except (pkcs11.exceptions.TokenNotRecognised, pkcs11.exceptions.TokenNotPresent):
                continue  # empty reader slot / uninitialised token
            except Exception as e:
                problems.append(f"{drv.name}, slot {slot.slot_id}: {e.__class__.__name__} {e}".strip())
    # Usable certificates first, then by expiry.
    result.sort(key=lambda c: (not c.is_valid_now, not c.is_qualified, -c.not_after.timestamp()))
    return result, problems


def find_slot(tc: TokenCert):
    lib = pkcs11.lib(tc.driver.path)
    for slot in lib.get_slots(token_present=True):
        if slot.slot_id == tc.slot_id:
            return slot
    raise TokenError("The token was removed. Insert it and try again.")


def open_session(tc: TokenCert, pin: str | None):
    """Log in to the token holding ``tc``. ``pin=None`` uses the device's PIN pad."""
    token = find_slot(tc).get_token()
    try:
        if tc.protected_auth and not pin:
            return token.open(user_pin=pkcs11.PROTECTED_AUTH)
        return token.open(user_pin=pin)
    except pkcs11.exceptions.PinIncorrect:
        raise TokenError("Wrong PIN.") from None
    except pkcs11.exceptions.PinLocked:
        raise TokenError("The token PIN is locked.") from None
    except (pkcs11.exceptions.PinLenRange, pkcs11.exceptions.PinInvalid):
        raise TokenError("The PIN has an invalid length or characters.") from None
    except pkcs11.exceptions.PinExpired:
        raise TokenError("The PIN has expired; change it with the vendor's token tool.") from None
    except pkcs11.exceptions.TokenNotPresent:
        raise TokenError("The token was removed. Insert it and try again.") from None
