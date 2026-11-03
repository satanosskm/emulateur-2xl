"""Compilation de l'exécutable Windows avec PyInstaller.

Usage :
    python tools/build.py                # depuis Windows
    python tools/build.py --wine         # depuis Linux, via Wine

Le script produit ``dist/Emulateur-2XL.exe`` (Windows) ou ``dist/2XL-FR`` (Unix).
Aucune piste audio n'est incluse : l'utilisateur fournit ses propres MP3.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_NAME = "Emulateur-2XL"
ENTRY = ROOT / "tools" / "launcher.py"
ASSETS = ROOT / "assets"


def build_command() -> list[str]:
    separator = ";" if os.name == "nt" else ":"
    args = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name",
        APP_NAME,
        "--paths",
        str(ROOT),
        "--collect-data",
        "soundfile",
        "--collect-binaries",
        "soundfile",
        "--hidden-import",
        "soundfile",
        "--hidden-import",
        "sounddevice",
        "--hidden-import",
        "numpy",
        "--exclude-module",
        "pytest",
        "--exclude-module",
        "matplotlib",
    ]
    if ASSETS.is_dir():
        for item in sorted(ASSETS.iterdir()):
            if item.is_file():
                args += ["--add-data", f"{item}{separator}assets"]
    args.append(str(ENTRY))
    if sys.platform.startswith("win"):
        icon = ASSETS / "2xl.ico"
        if icon.is_file():
            args += ["--icon", str(icon)]
    return args


def main() -> int:
    if not ENTRY.is_file():
        print(f"Entrée introuvable : {ENTRY}", file=sys.stderr)
        return 2
    command = build_command()
    print(" ".join(command))
    result = subprocess.run(command, cwd=ROOT, check=False)
    if result.returncode != 0:
        return result.returncode
    produced = ROOT / "dist" / (f"{APP_NAME}.exe" if os.name == "nt" else APP_NAME)
    if produced.exists():
        size = produced.stat().st_size / (1024 * 1024)
        print(f"\nOK : {produced} ({size:.1f} Mio)")
    else:
        print("Compilation terminée, mais aucun binaire trouvé dans dist/.", file=sys.stderr)
        return 1
    # Copie le dossier de cassettes vide à côté du binaire Windows.
    bundle = ROOT / "dist" / "cassettes"
    bundle.mkdir(parents=True, exist_ok=True)
    readme = bundle / "LISEZ-MOI.txt"
    if not readme.exists():
        readme.write_text(
            "Place ici un sous-dossier par cassette, avec quatre fichiers :\n"
            "Track1.mp3, Track2.mp3, Track3.mp3, Track4.mp3\n"
            "correspondant à OUI, NON, VRAI et FAUX.\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# `--wine` est réservé aux mainteneurs : il est traité par build_windows.sh,
# qui appelle ce script depuis l'interpréteur Python Windows.
_ = shutil
_ = argparse
