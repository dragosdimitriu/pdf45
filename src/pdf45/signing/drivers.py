# SPDX-License-Identifier: GPL-3.0-or-later
"""Known locations of PKCS#11 drivers for the tokens used with qualified certificates.

Romanian qualified certificates (certSIGN, DigiSign, Trans Sped, ...) are
delivered on SafeNet eToken or Thales/Gemalto IDPrime tokens; the vendor's
middleware installs a PKCS#11 module at one of the paths below.  Anything
not listed can be added by hand in the signature settings.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Driver:
    name: str
    path: str


_WINDOWS = [
    ("SafeNet eToken (SafeNet Authentication Client)", r"C:\Windows\System32\eTPKCS11.dll"),
    ("Thales/Gemalto IDPrime", r"C:\Windows\System32\IDPrimePKCS11.dll"),
    ("Thales/Gemalto IDPrime", r"C:\Program Files\Gemalto\IDGo 800 PKCS#11\IDPrimePKCS1164.dll"),
    ("Thales/Gemalto IDPrime", r"C:\Program Files (x86)\Gemalto\IDGo 800 PKCS#11\IDPrimePKCS1164.dll"),
    ("Bit4id", r"C:\Windows\System32\bit4xpki.dll"),
    ("IDEMIA / Oberthur AWP", r"C:\Windows\System32\aetpkss1.dll"),
    ("OpenSC", r"C:\Program Files\OpenSC Project\OpenSC\pkcs11\opensc-pkcs11.dll"),
    ("SoftHSM2 (testing)", r"C:\SoftHSM2\lib\softhsm2-x64.dll"),
]

_LINUX = [
    ("SafeNet eToken (SafeNet Authentication Client)", "/usr/lib/libeTPkcs11.so"),
    ("SafeNet eToken (SafeNet Authentication Client)", "/usr/lib/x86_64-linux-gnu/libeTPkcs11.so"),
    ("Thales/Gemalto IDPrime", "/usr/lib/libIDPrimePKCS11.so"),
    ("Thales/Gemalto IDPrime", "/usr/lib/x86_64-linux-gnu/libIDPrimePKCS11.so"),
    ("Bit4id", "/usr/lib/bit4id/libbit4xpki.so"),
    ("IDEMIA / Oberthur AWP", "/usr/lib/libOcsCryptoki.so"),
    ("OpenSC", "/usr/lib/x86_64-linux-gnu/opensc-pkcs11.so"),
    ("OpenSC", "/usr/lib/x86_64-linux-gnu/pkcs11/opensc-pkcs11.so"),
    ("SoftHSM2 (testing)", "/usr/lib/softhsm/libsofthsm2.so"),
    ("SoftHSM2 (testing)", "/usr/lib/x86_64-linux-gnu/softhsm/libsofthsm2.so"),
]


def known_drivers() -> list[Driver]:
    table = _WINDOWS if sys.platform.startswith("win") else _LINUX
    return [Driver(n, p) for n, p in table]


def installed_drivers(extra_paths: list[str] | None = None) -> list[Driver]:
    """Drivers present on this machine: the known ones plus user-configured paths."""
    found: list[Driver] = []
    seen: set[str] = set()
    candidates = known_drivers() + [Driver(Path(p).name, p) for p in (extra_paths or [])]
    for drv in candidates:
        key = str(Path(drv.path)).lower() if sys.platform.startswith("win") else str(Path(drv.path))
        if key in seen or not Path(drv.path).is_file():
            continue
        seen.add(key)
        found.append(drv)
    return found
