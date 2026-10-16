# SPDX-License-Identifier: GPL-3.0-or-later
"""Trust anchors for building and validating certificate chains.

Two sources are combined:

* the EU trusted lists (eIDAS): every qualified CA and timestamping
  authority in the member states, downloaded from the European
  Commission's list of the lists and cached on disk.  This is what makes
  certSIGN, DigiSign & co. trusted, and what tells us whether a signature
  is *qualified*;
* the operating system's root certificates (plus certifi's Mozilla
  bundle), for non-EU certificates and commercial timestamp servers.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import ssl
import sys
import threading
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

from asn1crypto import pem, x509
from pyhanko_certvalidator import ValidationContext
from pyhanko_certvalidator.authority import Authority, TrustAnchor
from pyhanko_certvalidator.registry import SimpleTrustManager, TrustManager

log = logging.getLogger(__name__)

TL_CACHE_DAYS = 7


def _patch_anchor_as_crl_issuer() -> None:
    """Let a non-self-signed trust anchor sign the CRL for its own certificates.

    EU trusted lists publish *issuing* CAs (e.g. "DigiSign Qualified CA
    Class 3 2017") as trust anchors, not their roots.  When such a CA signs
    the CRL of the end-entity certificate, pyhanko-certvalidator tries to
    re-anchor the CRL issuer under *its* issuer, which is not in the path,
    and gives up ("chain of trust for the CRL issuer could not be
    determined").  The CRL issuer *is* the trust anchor, so the path to it
    is just the anchor itself.  The same happens with roots that are also
    published cross-signed (e.g. DigiCert Trusted Root G4).
    """
    from pyhanko_certvalidator.authority import AuthorityWithCert
    from pyhanko_certvalidator.path import ValidationPath

    original = ValidationPath.truncate_to_issuer_and_append
    if getattr(original, "_pdf45_patched", False):
        return

    def truncate_to_issuer_and_append(self, cert):
        auth = self.trust_anchor.authority
        if isinstance(auth, AuthorityWithCert):
            anchor = auth.certificate
            # The same CA may also appear as a cross-signed or re-issued certificate
            # (same name and key, different issuer): that is still the anchor.
            if anchor.sha256 == cert.sha256 or (
                    anchor.subject == cert.subject and anchor.public_key.dump() == cert.public_key.dump()):
                return ValidationPath(self._root, interm=[], leaf=None)
        return original(self, cert)

    truncate_to_issuer_and_append._pdf45_patched = True
    ValidationPath.truncate_to_issuer_and_append = truncate_to_issuer_and_append


_patch_anchor_as_crl_issuer()


# ------------------------------------------------------------- system roots
def _load_pem_or_der(data: bytes) -> list[x509.Certificate]:
    out = []
    if pem.detect(data):
        for _, _, der in pem.unarmor(data, multiple=True):
            try:
                out.append(x509.Certificate.load(der))
            except Exception:
                pass
    else:
        try:
            out.append(x509.Certificate.load(data))
        except Exception:
            pass
    return out


def system_roots() -> list[x509.Certificate]:
    certs: list[x509.Certificate] = []
    if sys.platform.startswith("win"):
        try:
            for der, enc, _trust in ssl.enum_certificates("ROOT"):
                if enc == "x509_asn":
                    certs.extend(_load_pem_or_der(der))
        except Exception as e:
            log.warning("cannot read the Windows root store: %s", e)
    else:
        for path in ("/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt",
                     "/etc/ssl/ca-bundle.pem"):
            p = Path(path)
            if p.is_file():
                certs.extend(_load_pem_or_der(p.read_bytes()))
                break
    try:
        import certifi

        certs.extend(_load_pem_or_der(Path(certifi.where()).read_bytes()))
    except Exception:
        pass
    seen, unique = set(), []
    for c in certs:
        h = hashlib.sha256(c.dump()).digest()
        if h not in seen:
            seen.add(h)
            unique.append(c)
    return unique


# ------------------------------------------------------- EU trusted lists
class EUTrustedLists:
    """Lazily downloads the EU trusted lists once per process (cached on disk)."""

    def __init__(self, cache_dir: Path | None, territories: set[str] | None = None):
        self.cache_dir = cache_dir
        self.territories = territories  # None = all member states
        self._registry = None
        self._error: str | None = None
        self._lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        return self._registry is not None

    @property
    def error(self) -> str | None:
        return self._error

    def registry(self):
        """Return the TSPRegistry, downloading it if needed (blocking; call from a worker)."""
        with self._lock:
            if self._registry is None:
                try:
                    self._registry = _run_isolated(self._fetch)
                    self._error = None
                except Exception as e:
                    self._error = f"{e.__class__.__name__}: {e}"
                    log.warning("EU trusted lists unavailable: %s", self._error)
            return self._registry

    async def _fetch(self):
        import aiohttp
        from pyhanko.sign.validation.qualified.eutl_fetch import (
            FileSystemTLCache,
            InMemoryTLCache,
            lotl_to_registry,
        )

        if self.cache_dir is not None:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            cache = FileSystemTLCache(self.cache_dir, timedelta(days=TL_CACHE_DAYS))
        else:
            cache = InMemoryTLCache()
        timeout = aiohttp.ClientTimeout(total=180)
        async with aiohttp.ClientSession(timeout=timeout) as client:
            registry, errors = await lotl_to_registry(
                None, client, cache=cache, only_territories=self.territories
            )
        if errors:
            log.info("%d trusted-list entries could not be parsed (ignored)", len(errors))
        return registry


def _run_isolated(coro_fn):
    """asyncio.run() that also works when called from inside a running event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro_fn())
    box: dict = {}

    def target():
        try:
            box["result"] = asyncio.run(coro_fn())
        except BaseException as e:  # re-raised in the caller's thread
            box["error"] = e

    t = threading.Thread(target=target, name="pdf45-eutl")
    t.start()
    t.join()
    if "error" in box:
        raise box["error"]
    return box["result"]


# ------------------------------------------------------- trust manager
def _eu_trust_manager(registry) -> TrustManager:
    """Trust manager over the EU lists that also anchors at timestamping services.

    pyHanko's TSPTrustManager only offers qualified *CAs* as issuers, so the
    certificate of a qualified timestamping unit (e.g. "Sectigo Qualified Time
    Stamping Signer", issued by the QTST service "Sectigo Qualified Time
    Stamping CA R35") never finds its anchor and long-term validation of the
    timestamp fails.  Offer the QTST service identities as issuers too.
    """
    from pyhanko.sign.validation.qualified.tsp import TSPTrustManager

    class EUTrustManager(TSPTrustManager):
        def find_potential_issuers(self, cert):
            yield from super().find_potential_issuers(cert)
            for authority in self.tsp_registry.known_timestamp_authorities:
                anchor = self.as_trust_anchor(authority)
                if anchor and authority.is_potential_issuer_of(cert):
                    yield anchor

    return EUTrustManager(registry)


class CombinedTrustManager(TrustManager):
    """First manager that recognises an authority wins (EU lists before system roots)."""

    def __init__(self, *managers: TrustManager):
        self.managers = [m for m in managers if m is not None]

    def as_trust_anchor(self, authority: Authority) -> TrustAnchor | None:
        for m in self.managers:
            anchor = m.as_trust_anchor(authority)
            if anchor is not None:
                return anchor
        return None

    def find_potential_issuers(self, cert: x509.Certificate) -> Iterator[TrustAnchor]:
        for m in self.managers:
            yield from m.find_potential_issuers(cert)


class TrustStore:
    """Everything a signer/validator needs to build a ValidationContext."""

    def __init__(self, cache_dir: Path | None = None, territories: set[str] | None = None,
                 extra_roots: list[x509.Certificate] | None = None):
        self.eu = EUTrustedLists(cache_dir, territories)
        self._system: SimpleTrustManager | None = None
        self.extra_roots = extra_roots or []

    def system_manager(self) -> SimpleTrustManager:
        if self._system is None:
            self._system = SimpleTrustManager.build(trust_roots=system_roots() + self.extra_roots)
        return self._system

    def trust_manager(self, use_eu: bool = True) -> TrustManager:
        managers: list[TrustManager] = []
        if use_eu:
            reg = self.eu.registry()
            if reg is not None:
                managers.append(_eu_trust_manager(reg))
        managers.append(self.system_manager())
        return CombinedTrustManager(*managers)

    def eu_registry(self):
        return self.eu.registry()

    def validation_context(self, *, use_eu: bool = True, allow_fetching: bool = True,
                           revocation_mode: str = "soft-fail",
                           other_certs: list[x509.Certificate] | None = None) -> ValidationContext:
        return ValidationContext(
            trust_manager=self.trust_manager(use_eu),
            allow_fetching=allow_fetching,
            revocation_mode=revocation_mode,
            other_certs=other_certs or [],
        )
