"""Point d'entrée en ligne de commande."""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
from pathlib import Path

from . import __app_author__, __app_name__, __version__
from .engine import TRACK_COUNT, CassetteError, inspect_track
from .library import (
    TRACK_LABELS,
    detect_track_paths,
    load_cassette,
    scan_library,
)


def _attach_console() -> None:
    """Rend la sortie visible quand l'exécutable est compilé sans console.

    Lancé depuis une invite de commandes, un programme fenêtré n'a pas de
    sortie standard. On se rattache à la console du terminal appelant afin que
    ``2XL-FR.exe check`` affiche réellement son diagnostic.
    """
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return
    with contextlib.suppress(Exception):
        import ctypes

        ATTACH_PARENT_PROCESS = -1
        if ctypes.windll.kernel32.AttachConsole(ATTACH_PARENT_PROCESS):
            # Flux volontairement conservés jusqu'à la fin du processus.
            console_out = open(  # noqa: SIM115
                "CONOUT$", "w", encoding="utf-8", errors="replace", buffering=1
            )
            console_err = open(  # noqa: SIM115
                "CONOUT$", "w", encoding="utf-8", errors="replace", buffering=1
            )
            sys.stdout, sys.stderr = console_out, console_err
            with contextlib.suppress(Exception):
                ctypes.windll.kernel32.SetConsoleOutputCP(65001)


def _print(text: str = "") -> None:
    with contextlib.suppress(Exception):
        print(text)


def cmd_library(_args: argparse.Namespace) -> int:
    cassettes = scan_library()
    if not cassettes:
        _print("Aucune cassette trouvée dans le dossier « cassettes » de l'application.")
        return 1
    _print(f"{len(cassettes)} cassette(s) :")
    for cassette in cassettes:
        _print(f"  · {cassette.name} — {cassette.duration_s / 60:.1f} min — {cassette.folder}")
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    target = Path(args.folder)
    try:
        paths = detect_track_paths(target)
    except CassetteError as exc:
        _print(f"Erreur : {exc}")
        return 2
    _print(f"Dossier : {target}")
    for index, path in enumerate(paths):
        info = inspect_track(path, index, TRACK_LABELS[index])
        _print(
            f"  {index + 1}. {TRACK_LABELS[index]:<5} {info.filename:<24} "
            f"{info.duration_s / 60:6.2f} min  {info.samplerate} Hz  {info.channels} canal(aux)"
        )
    try:
        cassette = load_cassette(target)
    except CassetteError as exc:
        _print(f"Cassette non exploitable : {exc}")
        return 2
    warnings = cassette.warnings()
    _print("Cassette valide." if not warnings else "Avertissements :")
    for warning in warnings:
        _print(f"  ! {warning}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Vérifie l'installation : bibliothèques, audio, image, cassettes."""
    _print(f"{__app_name__} {__version__} — par {__app_author__}")
    problems = 0
    try:
        import tkinter

        _print(f"  Tkinter ......... OK ({tkinter.TkVersion})")
    except Exception as exc:  # pragma: no cover
        _print(f"  Tkinter ......... ÉCHEC ({exc})")
        problems += 1
    try:
        import soundfile

        _print(f"  soundfile ....... OK ({soundfile.__version__}, libsndfile {soundfile.__libsndfile_version__})")
        if "MP3" not in soundfile.available_formats():
            _print("  MP3 ............. INDISPONIBLE (libsndfile sans support MP3)")
            problems += 1
        else:
            _print("  MP3 ............. OK")
    except Exception as exc:  # pragma: no cover
        _print(f"  soundfile ....... ÉCHEC ({exc})")
        problems += 1
    try:
        import sounddevice

        devices = sounddevice.query_devices()
        _print(f"  PortAudio ....... OK ({sounddevice.__version__}, {len(devices)} périphériques)")
    except Exception as exc:  # pragma: no cover
        _print(f"  PortAudio ....... ÉCHEC ({exc})")
        problems += 1
    try:
        from PIL import Image

        _print(f"  Pillow .......... OK ({Image.__version__})")
    except Exception as exc:  # pragma: no cover
        _print(f"  Pillow .......... ÉCHEC ({exc})")
        problems += 1

    if args.folder:
        result = cmd_inspect(args)
        if result:
            problems += 1
    else:
        cassettes = scan_library()
        _print(f"  Cassettes ....... {len(cassettes)} trouvée(s)")
    _print("Diagnostic terminé." if not problems else f"Diagnostic terminé : {problems} problème(s).")
    return 0 if not problems else 1


def cmd_hotspot_test(args: argparse.Namespace) -> int:
    """Vérifie sans souris que chaque bouton de la photo sélectionne sa piste."""
    import tkinter as tk

    from . import ui

    root = tk.Tk()
    app = ui.TwoXLApp(root, autoload=False)
    root.update_idletasks()
    root.update()
    if args.folder:
        app.load_folder(args.folder)
        root.update()
    if not app.engine.is_loaded:
        library = scan_library()
        if library:
            app.load(library[0])
            root.update()
    if not app.engine.is_loaded:
        print("Aucune cassette chargée : les boutons resteraient inactifs (comportement voulu).")
        root.destroy()
        return 2
    if not app.hotspot_rects:
        print("Repères indisponibles : la photo du robot est-elle présente ?")
        root.destroy()
        return 1
    failures = 0
    for index, rect in enumerate(app.hotspot_rects):
        x = (rect[0] + rect[2]) / 2
        y = (rect[1] + rect[3]) / 2
        found = app._spot_at(x, y)
        if found != index:
            print(f"  bouton {index + 1} : repère introuvable au centre ({found})")
            failures += 1
            continue
        before = app.selection_count
        class _Event:
            pass

        event = _Event()
        event.x, event.y = x, y
        app._on_canvas_motion(event)
        app._on_canvas_click(event)
        root.update()
        if app.selection_count != before + 1 or app.engine.active_track != index:
            print(f"  bouton {index + 1} ({TRACK_LABELS[index]}) : sélection non appliquée")
            failures += 1
        else:
            print(f"  bouton {index + 1} ({TRACK_LABELS[index]}) : OK")
    # un clic hors des boutons ne doit rien changer
    before = app.selection_count
    outside = type("E", (), {"x": 2, "y": 2})()
    app._on_canvas_click(outside)
    root.update()
    if app.selection_count != before:
        print("  clic hors zone : sélection inattendue")
        failures += 1
    app.engine.close()
    root.destroy()
    print("Zones cliquables valides." if not failures else f"{failures} problème(s).")
    return 0 if not failures else 1


def cmd_run(args: argparse.Namespace) -> int:
    from . import ui

    return ui.run(autoload=not args.no_autoload, folder=args.folder)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="2xl",
        description=f"{__app_name__} — cassettes françaises à 4 pistes. Projet de {__app_author__}.",
    )
    parser.add_argument("--version", action="version", version=f"{__app_name__} {__version__}")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="ouvre l'interface graphique (par défaut)")
    run.add_argument("folder", nargs="?", help="dossier contenant les quatre pistes")
    run.add_argument("--no-autoload", action="store_true", help="ne charge aucune cassette au démarrage")
    run.set_defaults(func=cmd_run)

    library = sub.add_parser("library", help="liste les cassettes installées")
    library.set_defaults(func=cmd_library)

    inspect_cmd = sub.add_parser("inspect", help="analyse un dossier de quatre pistes")
    inspect_cmd.add_argument("folder")
    inspect_cmd.set_defaults(func=cmd_inspect)

    hotspot = sub.add_parser("test-hotspots", help="vérifie les boutons de la photo (sans souris)")
    hotspot.add_argument("folder", nargs="?", help="cassette à utiliser pour le test")
    hotspot.set_defaults(func=cmd_hotspot_test)

    check = sub.add_parser("check", help="vérifie l'installation et l'audio")
    check.add_argument("folder", nargs="?", help="optionnel : analyse aussi ce dossier")
    check.set_defaults(func=cmd_check)

    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in {"run", "-h", "--help", "--version"}:
        _attach_console()
    parser = build_parser()
    if not argv:
        argv = ["run"]
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 0
    try:
        return int(args.func(args))
    except CassetteError as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


# Import tardif volontaire pour garder `--version` rapide.
_ = TRACK_COUNT
