# SPDX-License-Identifier: GPL-3.0-or-later
"""Text shown in Help -> Credits and Help -> Help."""

HELP_TEXT = "You are welcome, Adobe!"

# Every free / open-source component used to build, test or package pdf/45.
# Keep this list in sync with pyproject.toml, the CI workflow and packaging/.
CREDITS = [
    ("Python", "Python Software Foundation License", "https://www.python.org/",
     "The language the viewer is written in."),
    ("Qt 6 (QtPdf, QtPdfWidgets, QtPrintSupport, QtWidgets)", "LGPL-3.0", "https://www.qt.io/",
     "Cross-platform GUI toolkit; its PDF module provides rendering, text search and outline extraction."),
    ("PDFium", "BSD-3-Clause", "https://pdfium.googlesource.com/pdfium/",
     "The PDF rendering engine embedded in Qt's PDF module."),
    ("PySide6 (Qt for Python)", "LGPL-3.0", "https://doc.qt.io/qtforpython-6/",
     "Python bindings for Qt."),
    ("shiboken6", "LGPL-3.0", "https://doc.qt.io/qtforpython-6/shiboken6/",
     "Binding generator and runtime used by PySide6."),
    ("pyHanko", "MIT", "https://github.com/MatthiasValvekens/pyHanko",
     "PAdES signing, timestamps, long-term validation, signature validation and EU trusted-list processing."),
    ("pyhanko-certvalidator", "MIT", "https://github.com/MatthiasValvekens/certvalidator",
     "Certificate path building and OCSP / CRL revocation checking."),
    ("python-pkcs11", "MIT", "https://github.com/pyauth/python-pkcs11",
     "Talks to smart cards and USB tokens through their PKCS#11 driver."),
    ("asn1crypto", "MIT", "https://github.com/wbond/asn1crypto",
     "Parses certificates, CMS signatures, OCSP responses and CRLs."),
    ("cryptography", "Apache-2.0 OR BSD-3-Clause", "https://cryptography.io/",
     "Cryptographic primitives (signature verification, hashing)."),
    ("aiohttp", "Apache-2.0", "https://docs.aiohttp.org/",
     "Downloads trusted lists, OCSP / CRL data and timestamps."),
    ("lxml, xsdata, signxml", "BSD-3-Clause, MIT, Apache-2.0", "https://lxml.de/",
     "Read and verify the signed XML of the EU trusted lists."),
    ("pypdfium2", "Apache-2.0 OR BSD-3-Clause", "https://github.com/pypdfium2-team/pypdfium2",
     "Draws visible signatures on the page (PDFium with its form environment)."),
    ("Pillow", "MIT-CMU", "https://python-pillow.org/",
     "Image handling for the logo in visible signatures."),
    ("fontTools, HarfBuzz (uharfbuzz)", "MIT, MIT/Apache-2.0", "https://github.com/fonttools/fonttools",
     "Embed and shape the font used in visible signatures."),
    ("DejaVu Sans font", "Bitstream Vera / DejaVu license (free)", "https://dejavu-fonts.github.io/",
     "Font of the visible signature text (full Romanian diacritics)."),
    ("certifi, tzlocal, tzdata", "MPL-2.0, MIT, Apache-2.0", "https://github.com/certifi/python-certifi",
     "Root certificates and time-zone data."),
    ("SoftHSM2", "BSD-2-Clause", "https://www.opendnssec.org/softhsm/",
     "Software token used by the automated signing tests."),
    ("PyInstaller", "GPL-2.0-or-later with bootloader exception", "https://pyinstaller.org/",
     "Bundles the application into stand-alone executables for Windows and Linux."),
    ("Inno Setup", "Inno Setup License (free software)", "https://jrsoftware.org/isinfo.php",
     "Builds the Windows installer."),
    ("dpkg / Debian packaging tools", "GPL-2.0-or-later", "https://www.debian.org/",
     "Builds the Linux .deb installer."),
    ("pytest", "MIT", "https://pytest.org/",
     "Test runner for the automated smoke tests."),
    ("ReportLab", "BSD-3-Clause", "https://www.reportlab.com/opensource/",
     "Generates the sample PDF used by the test suite."),
    ("pypdf", "BSD-3-Clause", "https://pypdf.readthedocs.io/",
     "Verifies printed-to-PDF output in the test suite."),
    ("GitHub Actions runner images", "MIT", "https://github.com/actions/runner-images",
     "Continuous-integration environment that builds the installers."),
]


def _render() -> str:
    rows = "".join(
        f"<li><b>{name}</b> &mdash; {licence}<br>"
        f"<a href='{url}'>{url}</a><br><i>{note}</i></li>"
        for name, licence, url, note in CREDITS
    )
    return (
        "<p><b>@Ciutalii boemi - o eternă sursă de inspirație!</b></p>"
        "<p>pdf/45 is free software (GPL-3.0-or-later). It exists thanks to "
        "these free and open-source projects:</p>"
        f"<ul>{rows}</ul>"
        "<p>Thank you to everyone who maintains them.</p>"
    )


CREDITS_HTML = _render()
