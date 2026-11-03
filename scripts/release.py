"""Prépare une publication GitHub : archive du programme et brouillon de release.

Usage (depuis la racine du dépôt, exécutable déjà compilé dans dist/) :

    python scripts/release.py

Le script lit la version dans twoxl/__init__.py, vérifie que dist/Emulateur-2XL.exe existe,
construit Emulateur-2XL-<version>-windows.zip et écrit docs/note-de-version.md.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXE = ROOT / "dist" / "Emulateur-2XL.exe"
FILES = ["README.md", "LICENSE", "LISEZ-MOI.txt"]


def version() -> str:
    for line in (ROOT / "twoxl" / "__init__.py").read_text(encoding="utf-8").splitlines():
        if line.startswith("__version__"):
            return line.split("=")[1].strip().strip('"')
    raise SystemExit("Version introuvable dans twoxl/__init__.py")


def main() -> None:
    if not EXE.exists():
        raise SystemExit("Compile d'abord l'exécutable : python tools/build.py")

    tag = version()
    archive = ROOT / f"Emulateur-2XL-{tag}-windows.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.write(EXE, "Emulateur-2XL/Emulateur-2XL.exe")
        for name in FILES:
            bundle.write(ROOT / name, f"Emulateur-2XL/{name}")
        bundle.writestr("Emulateur-2XL/cassettes/LISEZ-MOI.txt", "Place ici un sous-dossier par cassette.\n")

    note = ROOT / "docs" / "note-de-version.md"
    note.parent.mkdir(exist_ok=True)
    note.write_text(
        f"# Émulateur robot 2-XL {tag}\n\n"
        "## Téléchargement\n\n"
        f"Décompresse `{archive.name}` puis lance `Emulateur-2XL.exe`.\n\n"
        "## Nouveautés\n\n"
        "- Boutons A, B et C affichés à côté des réponses NON, VRAI et FAUX.\n"
        "- Bouton principal qui alterne entre Lecture, Pause et Reprendre.\n"
        "- Interface recentrée et palette plus sobre.\n"
        "- Nom du projet : Émulateur robot 2-XL.\n",
        encoding="utf-8",
    )
    print(f"Archive : {archive}")
    print(f"Note de version : {note}")


if __name__ == "__main__":
    main()
