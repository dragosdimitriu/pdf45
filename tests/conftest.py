# SPDX-License-Identifier: GPL-3.0-or-later
import os
import tempfile

# Run the GUI without a display server (CI, containers).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, QStandardPaths  # noqa: E402

# Never read or write the user's real settings (window layout, recent files, ...).
QStandardPaths.setTestModeEnabled(True)
QSettings.setDefaultFormat(QSettings.Format.IniFormat)
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp(prefix="pdf45-test-"))

from PySide6.QtCore import QCoreApplication  # noqa: E402

QCoreApplication.setOrganizationName("pdf45-tests")
QCoreApplication.setApplicationName("pdf45-tests")
