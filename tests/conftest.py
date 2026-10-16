# SPDX-License-Identifier: GPL-3.0-or-later
import os

# Run the GUI without a display server (CI, containers).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
