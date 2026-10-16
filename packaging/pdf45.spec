# -*- mode: python ; coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# PyInstaller spec. Build from the repository root:
#   pyinstaller --noconfirm packaging/pdf45.spec
# Produces dist/pdf45/ (one-folder build; faster startup than one-file and
# what both installers expect).

import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent
SRC = ROOT / "src"
RES = SRC / "pdf45" / "resources"

is_windows = sys.platform.startswith("win")

a = Analysis(
    [str(SRC / "pdf45" / "__main__.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=[(str(RES), "pdf45/resources")],
    hiddenimports=["PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtPrintSupport"],
    hookspath=[],
    runtime_hooks=[],
    # Trim Qt modules the viewer never loads; keeps the bundle ~2x smaller.
    excludes=[
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
        "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets", "PySide6.QtQuick3D",
        "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DAnimation", "PySide6.Qt3DExtras",
        "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtCharts",
        "PySide6.QtDataVisualization", "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtPositioning",
        "PySide6.QtLocation", "PySide6.QtSensors", "PySide6.QtSerialPort", "PySide6.QtSerialBus",
        "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtSql", "PySide6.QtTest",
        "PySide6.QtTextToSpeech", "PySide6.QtWebSockets", "PySide6.QtWebChannel", "PySide6.QtHttpServer",
        "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtUiTools", "PySide6.QtOpenGL",
        "PySide6.QtOpenGLWidgets", "PySide6.QtSpatialAudio", "PySide6.QtGraphs", "PySide6.QtStateMachine",
        "PySide6.QtNetworkAuth", "PySide6.QtPdfQuick", "PySide6.QtAsyncio",
        "tkinter", "unittest", "pydoc", "doctest", "test",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="pdf45",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(RES / ("pdf45.ico" if is_windows else "pdf45.png")),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="pdf45",
)
