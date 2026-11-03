"""Point d'entrée empaqueté par PyInstaller."""

from __future__ import annotations

import multiprocessing
import sys

from twoxl.__main__ import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
