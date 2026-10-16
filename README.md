<p align="center"><img src="src/pdf45/resources/pdf45.png" alt="pdf/45 logo" width="160"></p>

# pdf/45

**pdf/45** ("pdf over 45") - a small, free PDF **viewer** for Windows and Linux. No cloud,
no accounts — it opens PDFs, lets you read, search, follow the outline, print
them and sign them with your hardware token.

Licensed under the **GNU GPL v3 or later**. See `LICENSE`.

## Features

- Continuous or single-page view, fit-width / fit-page / custom zoom
- Page navigation (toolbar, keyboard, Go-to-page, back/forward history)
- Full-text search with a results list and next/previous (`Ctrl+F`, `F3`)
- Outline / bookmarks panel
- Printing (page range, print to file) via the system print dialog
- Password-protected documents
- Recent files, drag & drop, open from the command line (`pdf45 file.pdf`)
- **Electronic signatures with a hardware token** (smart card / USB token via PKCS#11,
  e.g. certSIGN, DigiSign on SafeNet eToken or Thales IDPrime):
  - PAdES signatures, invisible or visible (drag the signature box on the page),
    or in an existing signature field
  - trusted timestamp (eIDAS-qualified Sectigo TSA by default, any RFC 3161 server)
  - long-term validation (LTV): certificate chain and OCSP/CRL data embedded (PAdES B-LTA)
- **Signature check** when a PDF is opened: integrity, changes after signing,
  certificate trust and revocation, timestamp, and qualified (QES) status from the
  EU trusted lists
- Help → Credits lists every free-software component the viewer is built on

## Build the installers

### Linux (.deb)

```bash
pip install -e ".[dev]"
pyinstaller --noconfirm packaging/pdf45.spec
packaging/linux/build_deb.sh            # -> dist/pdf45_<version>_amd64.deb
sudo apt install ./dist/pdf45_*.deb
```

Build on the oldest distribution you want to support (CI uses Ubuntu 22.04);
PyInstaller bundles cannot run on a glibc older than the build host's.

### Windows (setup.exe)

```powershell
pip install -e ".[dev]"
pyinstaller --noconfirm packaging\pdf45.spec
iscc /DAppVersion=1.0.0 packaging\windows\pdf45.iss   # -> dist\pdf45-1.0.0-setup.exe
```

Requires [Inno Setup 6](https://jrsoftware.org/isinfo.php) (`iscc` on PATH).
