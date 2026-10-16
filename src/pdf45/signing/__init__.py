# SPDX-License-Identifier: GPL-3.0-or-later
"""Electronic signatures with hardware tokens (PKCS#11) and signature validation.

The heavy lifting is done by pyHanko (PAdES signing, timestamps, LTV,
validation and EU trusted-list processing) and python-pkcs11 (access to
smart cards / USB tokens through the vendor's PKCS#11 driver).  This
package only wraps them in a small API the GUI can drive from a worker
thread.  Only ui.py imports Qt.
"""

import logging

# pyHanko logs every untrusted chain with a full traceback; the GUI reports
# the outcome itself, so keep only real errors.
logging.getLogger("pyhanko").setLevel(logging.ERROR)
logging.getLogger("pyhanko_certvalidator").setLevel(logging.ERROR)
# aiohttp sessions owned by the revocation fetchers are reported as "unclosed" at exit.
logging.getLogger("asyncio").setLevel(logging.CRITICAL)
