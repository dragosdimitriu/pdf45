# SPDX-License-Identifier: GPL-3.0-or-later
"""Entry point: ``python -m pdf45 [file.pdf]``."""

import sys

from pdf45.app import main

if __name__ == "__main__":
    sys.exit(main(sys.argv))
