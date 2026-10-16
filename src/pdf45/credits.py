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
        "<p>pdf/45 is free software (GPL-3.0-or-later). It exists thanks to "
        "these free and open-source projects:</p>"
        f"<ul>{rows}</ul>"
        "<p>Thank you to everyone who maintains them.</p>"
    )


CREDITS_HTML = _render()
