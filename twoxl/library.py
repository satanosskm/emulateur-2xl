"""Découverte des cassettes, configuration persistante et chemins."""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .engine import Cassette, CassetteError, build_cassette

TRACK_LABELS = ("OUI", "NON", "VRAI", "FAUX")
# Numéro affiché et nom du bouton sur le jouet :
#   1 : OUI (pas de lettre) — 2 : A / NON — 3 : B / VRAI — 4 : C / FAUX
TRACK_KEYS = ("", "A", "B", "C")
AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".ogg", ".oga", ".m4a", ".aiff", ".aif", ".opus"}
CONFIG_NAME = "config_2xl.json"
CONFIG_VERSION = 2

_LABEL_PATTERN = re.compile(r"^(?:piste|track|programme|prog|canal|voie)?[\s_\-]*([1-4])$", re.I)
_WORD_TO_INDEX = {"oui": 0, "non": 1, "vrai": 2, "faux": 3}


# --------------------------------------------------------------------------
# Chemins
# --------------------------------------------------------------------------


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def base_dir() -> Path:
    """Dossier de l'exécutable (ou de la racine du projet en développement)."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def asset_dir() -> Path:
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        candidate = Path(bundled) / "assets"
        if candidate.is_dir():
            return candidate
    return base_dir() / "assets"


def asset(name: str) -> Path:
    return asset_dir() / name


def cassettes_dir() -> Path:
    return base_dir() / "cassettes"


def user_data_dir() -> Path:
    if os.name == "nt":
        root = os.environ.get("APPDATA") or str(Path.home())
    elif sys.platform == "darwin":
        root = str(Path.home() / "Library" / "Application Support")
    else:
        root = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(root) / "2XL-FR"


def config_candidates() -> list[Path]:
    return [base_dir() / CONFIG_NAME, user_data_dir() / CONFIG_NAME]


# --------------------------------------------------------------------------
# Détection des fichiers d'une cassette
# --------------------------------------------------------------------------


def _natural_key(value: str) -> list:
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", value)]


def audio_files(folder: Path) -> list[Path]:
    try:
        files = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS]
    except OSError:
        return []
    return sorted(files, key=lambda p: _natural_key(p.name))


def detect_track_paths(folder: str | Path) -> list[Path]:
    """Trouve les quatre pistes d'un dossier, quel que soit leur nommage.

    Formats reconnus (insensibles à la casse) :
      * ``Track1.mp3`` … ``Track4.mp3``, ``Piste 1.mp3``, ``2.mp3`` ;
      * ``OUI.mp3``, ``NON.mp3``, ``VRAI.mp3``, ``FAUX.mp3`` ;
      * à défaut, exactement quatre fichiers audio classés naturellement.
    """
    path = Path(folder).expanduser()
    if not path.is_dir():
        raise CassetteError(f"Dossier introuvable : {path}")
    files = audio_files(path)
    if not files:
        raise CassetteError(
            f"Aucun fichier audio dans « {path.name} ». Formats acceptés : "
            + ", ".join(sorted(AUDIO_EXTENSIONS))
        )

    indexed: dict[int, Path] = {}
    by_word: dict[int, Path] = {}
    for file in files:
        stem = file.stem.strip()
        match = _LABEL_PATTERN.match(stem)
        if match and int(match.group(1)) not in indexed:
            indexed[int(match.group(1))] = file
            continue
        lowered = stem.casefold()
        for word, index in _WORD_TO_INDEX.items():
            if lowered.startswith(word) and index not in by_word:
                by_word[index] = file
                break

    if len(indexed) == 4:
        return [indexed[i] for i in range(1, 5)]
    if len(by_word) == 4:
        return [by_word[i] for i in range(4)]
    if len(files) == 4:
        return files
    raise CassetteError(
        f"Impossible d'identifier quatre pistes dans « {path.name} » "
        f"({len(files)} fichier(s) audio trouvé(s)). Nomme-les Track1.mp3 à "
        "Track4.mp3, Piste1.mp3 à Piste4.mp3, ou OUI/NON/VRAI/FAUX."
    )


def load_cassette(folder: str | Path, name: str | None = None) -> Cassette:
    path = Path(folder).expanduser().resolve()
    paths = detect_track_paths(path)
    return build_cassette(paths, name=name or path.name, folder=path)


def cassette_from_paths(paths: list[str | Path], name: str, folder: Path | None = None) -> Cassette:
    if len(paths) != 4:
        raise CassetteError("Sélectionne exactement quatre pistes.")
    return build_cassette(paths, name=name, folder=folder)


def cassette_from_record(record: dict) -> Cassette:
    """Reconstruit une cassette depuis la configuration (fichiers revalidés)."""
    tracks = record.get("tracks") or []
    if len(tracks) != 4:
        raise CassetteError("Entrée de configuration incomplète.")
    name = str(record.get("name") or "Cassette")
    folder_value = record.get("folder")
    folder = Path(folder_value) if folder_value else None
    return cassette_from_paths(tracks, name=name, folder=folder)


def scan_library(root: str | Path | None = None) -> list[Cassette]:
    directory = Path(root) if root else cassettes_dir()
    if not directory.is_dir():
        return []
    found: list[Cassette] = []
    seen: set[tuple[str, ...]] = set()
    for sub in sorted((p for p in directory.iterdir() if p.is_dir()), key=lambda p: _natural_key(p.name)):
        try:
            cassette = load_cassette(sub)
        except CassetteError:
            continue
        signature = tuple(str(t.path) for t in cassette.tracks)
        if signature in seen:
            continue
        seen.add(signature)
        found.append(cassette)
    return found


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


def cassette_record(cassette: Cassette) -> dict:
    return {
        "name": cassette.name,
        "folder": str(cassette.folder) if cassette.folder else None,
        "tracks": [str(t.path) for t in cassette.tracks],
    }


@dataclass
class AppConfig:
    volume: float = 1.0
    last_track: int = 0
    recent: list[dict] = field(default_factory=list)
    last_cassette: dict | None = None
    path: Path | None = None
    warning: str | None = None

    def remember(self, cassette: Cassette) -> None:
        record = cassette_record(cassette)
        signature = tuple(record["tracks"])
        self.recent = [r for r in self.recent if tuple(r.get("tracks", [])) != signature]
        self.recent.insert(0, record)
        self.recent = self.recent[:8]
        self.last_cassette = record

    @property
    def last_folder(self) -> Path | None:
        if self.last_cassette and self.last_cassette.get("folder"):
            folder = Path(self.last_cassette["folder"])
            if folder.is_dir():
                return folder
        return None


def _sanitize(raw: dict) -> AppConfig:
    config = AppConfig()
    try:
        config.volume = float(raw.get("volume", 1.0))
    except (TypeError, ValueError):
        config.volume = 1.0
    config.volume = max(0.0, min(1.0, config.volume))
    try:
        config.last_track = max(0, min(3, int(raw.get("last_track", 0))))
    except (TypeError, ValueError):
        config.last_track = 0

    def valid_record(record) -> bool:
        if not isinstance(record, dict):
            return False
        tracks = record.get("tracks")
        return isinstance(tracks, list) and len(tracks) == 4 and all(isinstance(t, str) for t in tracks)

    recent = raw.get("recent")
    if isinstance(recent, list):
        config.recent = [r for r in recent if valid_record(r)][:8]
    last = raw.get("last_cassette")
    if valid_record(last):
        config.last_cassette = last
    return config


def load_config() -> AppConfig:
    result = AppConfig()
    for candidate in config_candidates():
        if not candidate.is_file():
            continue
        try:
            with candidate.open("r", encoding="utf-8") as handle:
                raw = json.load(handle)
            if not isinstance(raw, dict):
                raise ValueError("objet JSON attendu")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            _quarantine(candidate)
            result = AppConfig(
                path=None,
                warning=f"Configuration illisible : valeurs par défaut rétablies ({exc}).",
            )
            continue
        config = _sanitize(raw)
        config.path = candidate
        return config
    return result


def _quarantine(path: Path) -> None:
    try:
        backup = path.with_suffix(path.suffix + ".bak")
        path.replace(backup)
    except OSError:
        pass


def save_config(config: AppConfig) -> Path | None:
    payload = {
        "version": CONFIG_VERSION,
        "volume": round(config.volume, 3),
        "last_track": config.last_track,
        "recent": config.recent,
        "last_cassette": config.last_cassette,
    }
    targets = [config.path] if config.path else []
    targets.extend(p for p in config_candidates() if p not in targets)
    for target in targets:
        if target is None:
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(target.suffix + ".tmp")
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            temporary.replace(target)
            config.path = target
            return target
        except OSError:
            continue
    return None
