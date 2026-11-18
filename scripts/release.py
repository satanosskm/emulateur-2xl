"""Prépare une publication GitHub : vérifie l'exécutable et liste les étapes.

Usage (depuis la racine du dépôt, exécutable déjà compilé dans dist/) :

    python scripts/release.py

La version publiée ne contient qu'un seul fichier, ``dist/Emulateur-2XL.exe``.
Le script rappelle la procédure et vérifie que tout est prêt.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXE = ROOT / "dist" / "Emulateur-2XL.exe"
NOTE = ROOT / "docs" / "note-de-version.md"


def version() -> str:
    for line in (ROOT / "twoxl" / "__init__.py").read_text(encoding="utf-8").splitlines():
        if line.startswith("__version__"):
            return line.split("=")[1].strip().strip('"')
    raise SystemExit("Version introuvable dans twoxl/__init__.py")


def main() -> None:
    tag = version()
    problems = []
    if not EXE.exists():
        problems.append("dist/Emulateur-2XL.exe est absent : lance python tools/build.py")
    if not NOTE.exists():
        problems.append("docs/note-de-version.md est absent")
    if problems:
        raise SystemExit("\n".join(problems))

    size = EXE.stat().st_size / (1024 * 1024)
    print(f"Version : {tag}")
    print(f"Fichier : {EXE} ({size:.1f} Mio)")
    print(f"Note     : {NOTE}")
    print()
    print("Publication :")
    print(f"  1. git tag v{tag} && git push origin v{tag}   (publication automatique)")
    print("  2. ou onglet Releases, Draft a new release, tag v" + tag)
    print("  3. titre : Émulateur robot 2-XL " + tag)
    print("  4. description : coller le contenu de docs/note-de-version.md")
    print("  5. joindre uniquement dist/Emulateur-2XL.exe")


if __name__ == "__main__":
    main()
