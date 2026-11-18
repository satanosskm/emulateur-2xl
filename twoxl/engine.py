"""Moteur audio à mémoire constante pour cassettes 2-XL (4 pistes).

Principe : les quatre pistes sont décodées *en continu* mais uniquement par
petits blocs, puis jetées. Rien n'est conservé en mémoire, contrairement à
l'ancienne version qui gardait quatre copies PCM complètes. Le changement de
piste est donc instantané et parfaitement synchronisé, comme les quatre têtes
de lecture de la cassette d'origine.

Le module est volontairement sans dépendance à l'interface graphique afin de
pouvoir être testé hors écran (tests unitaires, mode ``--check``).
"""

from __future__ import annotations

import contextlib
import math
import os
import queue
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

try:  # soundfile est indispensable à la lecture, mais l'app doit rester lançable
    import soundfile as sf
except Exception as exc:  # pragma: no cover - dépend de l'installation
    sf = None
    _SOUNDFILE_ERROR: Exception | None = exc
else:
    _SOUNDFILE_ERROR = None


DEFAULT_RATE = 44100
OUT_CHANNELS = 2
BLOCK_FRAMES = 1024
CANDIDATE_RATES = (44100, 48000, 32000)
FADE_SECONDS = 0.018
TRACK_COUNT = 4


class AudioUnavailable(RuntimeError):
    """Aucune sortie audio utilisable n'a pu être ouverte."""


class CassetteError(RuntimeError):
    """La cassette est incomplète ou illisible."""


# --------------------------------------------------------------------------
# Description d'une cassette
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class TrackInfo:
    """Métadonnées d'une piste, lues sans décoder l'audio."""

    index: int
    label: str
    path: Path
    samplerate: int
    channels: int
    frames: int

    @property
    def duration_s(self) -> float:
        return self.frames / float(self.samplerate)

    @property
    def filename(self) -> str:
        return self.path.name


@dataclass(frozen=True)
class Cassette:
    """Quatre pistes destinées à être lues ensemble."""

    name: str
    tracks: tuple[TrackInfo, ...]
    folder: Path | None = None

    @property
    def duration_s(self) -> float:
        """Durée utile : la plus courte des quatre pistes."""
        if not self.tracks:
            return 0.0
        return min(t.duration_s for t in self.tracks)

    @property
    def longest_s(self) -> float:
        if not self.tracks:
            return 0.0
        return max(t.duration_s for t in self.tracks)

    def warnings(self) -> list[str]:
        """Incohérences à signaler sans empêcher la lecture."""
        problems: list[str] = []
        if len(self.tracks) != TRACK_COUNT:
            problems.append(
                f"{len(self.tracks)} piste(s) détectée(s) au lieu de {TRACK_COUNT}."
            )
        if not self.tracks:
            return problems

        spread = self.longest_s - self.duration_s
        if spread > 1.0:
            problems.append(
                "Les pistes n'ont pas la même durée "
                f"({spread:.1f} s d'écart) : la cassette s'arrêtera à la fin "
                "de la plus courte."
            )
        rates = {t.samplerate for t in self.tracks}
        if len(rates) > 1:
            listed = ", ".join(str(r) for r in sorted(rates))
            problems.append(
                f"Fréquences d'échantillonnage différentes ({listed} Hz) : "
                "la synchronisation reste possible, mais vérifie l'encodage."
            )
        if any(t.channels > 2 for t in self.tracks):
            problems.append("Une piste a plus de deux canaux : seuls les deux premiers sont lus.")
        return problems


def inspect_track(path: str | Path, index: int, label: str) -> TrackInfo:
    """Lit les métadonnées d'un fichier audio (rapide, sans décodage complet)."""
    if sf is None:  # pragma: no cover
        raise CassetteError(
            "La bibliothèque soundfile est absente. Réinstalle l'application "
            "(pip install -r requirements.txt)."
        ) from _SOUNDFILE_ERROR
    file_path = Path(path).expanduser()
    if not file_path.is_file():
        raise CassetteError(f"Fichier introuvable : {file_path}")
    try:
        info = sf.info(str(file_path))
    except Exception as exc:
        raise CassetteError(f"Format audio illisible : {file_path.name} ({exc})") from exc
    if info.frames <= 0 or info.samplerate <= 0:
        raise CassetteError(f"Fichier audio vide : {file_path.name}")
    return TrackInfo(
        index=index,
        label=label,
        path=file_path.resolve(),
        samplerate=int(info.samplerate),
        channels=int(info.channels),
        frames=int(info.frames),
    )


def build_cassette(paths: list[str | Path], name: str, folder: Path | None = None) -> Cassette:
    """Construit une cassette à partir de quatre chemins, dans l'ordre OUI→FAUX."""
    if len(paths) != TRACK_COUNT:
        raise CassetteError(
            f"Une cassette 2-XL utilise exactement {TRACK_COUNT} pistes "
            f"({len(paths)} fournie(s))."
        )
    labels = ("OUI", "NON", "VRAI", "FAUX")
    tracks = tuple(inspect_track(p, i, labels[i]) for i, p in enumerate(paths))
    return Cassette(name=name, tracks=tracks, folder=folder)


# --------------------------------------------------------------------------
# Lecture d'une piste
# --------------------------------------------------------------------------


class _TrackReader:
    """Flux de décodage d'une piste, aligné sur l'horloge de sortie."""

    def __init__(self, track: TrackInfo, out_rate: int) -> None:
        self.track = track
        self.out_rate = out_rate
        self.ratio = track.samplerate / float(out_rate)
        self._file = None
        self._next_src = 0.0  # position source exacte (frames, fractionnaire)
        self._at_end = False

    # -- cycle de vie ------------------------------------------------------
    @property
    def is_open(self) -> bool:
        return self._file is not None

    def open(self, out_frame: int = 0) -> None:
        if sf is None:  # pragma: no cover
            raise CassetteError("soundfile indisponible") from _SOUNDFILE_ERROR
        self.close()
        self._file = sf.SoundFile(str(self.track.path))
        self.seek(out_frame)

    def close(self) -> None:
        if self._file is not None:
            try:
                self._file.close()
            finally:
                self._file = None

    def seek(self, out_frame: int) -> None:
        if self._file is None:
            return
        source = max(0, int(round(out_frame * self.ratio)))
        source = min(source, max(0, self.track.frames - 1))
        self._file.seek(source)
        self._next_src = out_frame * self.ratio
        self._at_end = False

    # -- lecture -----------------------------------------------------------
    def read(self, frames: int) -> np.ndarray:
        """Renvoie ``frames`` échantillons stéréo à la fréquence de sortie."""
        out = np.zeros((frames, OUT_CHANNELS), dtype=np.float32)
        if self._file is None or self._at_end or frames <= 0:
            return out

        if abs(self.ratio - 1.0) < 1e-9:
            data = self._file.read(frames, dtype="float32", always_2d=True)
            if data.size:
                out[: len(data)] = _to_stereo(data)[: len(out)]
            if len(data) < frames:
                self._at_end = True
            return out

        start = int(math.floor(self._next_src))
        if self._file.tell() != start:
            self._file.seek(start)
        span = int(math.ceil(self._next_src + frames * self.ratio)) - start + 2
        data = self._file.read(max(span, 2), dtype="float32", always_2d=True)
        if not data.size:
            self._at_end = True
            return out
        data = _to_stereo(data)
        positions = self._next_src + np.arange(frames, dtype=np.float64) * self.ratio - start
        positions = np.clip(positions, 0, len(data) - 1)
        grid = np.arange(len(data), dtype=np.float64)
        for channel in range(OUT_CHANNELS):
            out[:, channel] = np.interp(positions, grid, data[:, channel]).astype(np.float32)
        self._next_src += frames * self.ratio
        if len(data) < span:
            self._at_end = True
        return out


def _to_stereo(data: np.ndarray) -> np.ndarray:
    """Convertit (n, canaux) en (n, 2) float32."""
    if data.ndim == 1:
        data = data[:, None]
    channels = data.shape[1]
    if channels == 1:
        return np.repeat(data, 2, axis=1).astype(np.float32, copy=False)
    if channels == 2:
        return data.astype(np.float32, copy=False)
    mono = data[:, :OUT_CHANNELS].astype(np.float32, copy=False)
    return mono


# --------------------------------------------------------------------------
# Sortie audio
# --------------------------------------------------------------------------


class Sink(Protocol):  # pragma: no cover - simple protocole
    samplerate: int
    is_running: bool

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def close(self) -> None: ...


def _selected_device(sd) -> int | str | None:
    raw = os.environ.get("TWOXL_DEVICE", "").strip()
    if not raw:
        return None
    if raw.isdigit():
        return int(raw)
    return raw


class PortAudioSink:
    """Sortie stéréo flottante via PortAudio (paquet sounddevice)."""

    def __init__(
        self,
        callback: Callable[[np.ndarray, int], None],
        on_status: Callable[[str], None] | None = None,
    ) -> None:
        try:
            import sounddevice as sd
        except Exception as exc:  # pragma: no cover - dépend du système
            raise AudioUnavailable(
                "PortAudio n'est pas disponible sur ce système. "
                "Sous Windows, utilise l'exécutable fourni ; sinon installe "
                "les pilotes audio et le paquet sounddevice."
            ) from exc

        self._sd = sd
        self._callback = callback
        self._status_cb = on_status
        self._stream = None
        self.samplerate = DEFAULT_RATE
        last_error: Exception | None = None
        device = _selected_device(sd)

        for rate in CANDIDATE_RATES:
            stream = None
            try:
                stream = sd.OutputStream(
                    samplerate=rate,
                    channels=OUT_CHANNELS,
                    dtype="float32",
                    blocksize=BLOCK_FRAMES,
                    callback=self._portaudio_callback,
                    device=device,
                )
                stream.start()
            except Exception as exc:  # périphérique occupé, taux refusé…
                last_error = exc
                if stream is not None:
                    with contextlib.suppress(Exception):
                        stream.close()
                continue
            self._stream = stream
            self.samplerate = int(stream.samplerate)
            break

        if self._stream is None:
            raise AudioUnavailable(
                "Impossible d'ouvrir une sortie audio "
                f"({last_error}). Vérifie qu'aucune autre application "
                "n'occupe le périphérique, ou choisis-en un autre avec la "
                "variable TWOXL_DEVICE."
            )

    # -- rappels PortAudio -------------------------------------------------
    def _portaudio_callback(self, outdata, frames, time_info, status) -> None:  # noqa: ANN001
        if status and self._status_cb is not None:
            self._status_cb(str(status))
        try:
            self._callback(np.asarray(outdata), frames)
        except Exception:  # jamais d'exception hors du callback
            outdata.fill(0.0)

    # -- API ---------------------------------------------------------------
    @property
    def is_running(self) -> bool:
        return bool(self._stream is not None and self._stream.active)

    def start(self) -> None:
        if self._stream is not None and not self._stream.active:
            self._stream.start()

    def stop(self) -> None:
        if self._stream is not None and self._stream.active:
            with contextlib.suppress(Exception):
                self._stream.stop()

    def close(self) -> None:
        if self._stream is not None:
            with contextlib.suppress(Exception):
                self._stream.stop()
                self._stream.close()
            self._stream = None


# --------------------------------------------------------------------------
# Moteur
# --------------------------------------------------------------------------


class AudioEngine:
    """Lecteur synchronisé de quatre pistes, à empreinte mémoire constante."""

    def __init__(self, sink_factory: Callable[..., Sink] | None = None) -> None:
        self._sink_factory = sink_factory or PortAudioSink
        self._sink: Sink | None = None
        self.samplerate = DEFAULT_RATE
        self._cassette: Cassette | None = None
        self._readers: list[_TrackReader] = []
        self._pos = 0
        self._playing = False
        self._paused = False
        self._stopping = False
        self._active = 0
        self._volume = 1.0
        self._gain_now = np.zeros(TRACK_COUNT, dtype=np.float32)
        self._gain_target = np.zeros(TRACK_COUNT, dtype=np.float32)
        self._gain_target[0] = 1.0
        self._commands: queue.SimpleQueue = queue.SimpleQueue()
        self._events: deque[tuple[str, str]] = deque(maxlen=64)
        self._events_lock = threading.Lock()
        self._io_lock = threading.RLock()
        self._closed = False
        # Vrai tant que la sortie audio répond ; faux si elle a échoué.
        self._stream_ok = False

    # -- état --------------------------------------------------------------
    @property
    def cassette(self) -> Cassette | None:
        return self._cassette

    @property
    def active_track(self) -> int:
        return self._active

    @property
    def is_playing(self) -> bool:
        return self._playing and not self._paused

    @property
    def is_paused(self) -> bool:
        return self._playing and self._paused

    @property
    def is_loaded(self) -> bool:
        return self._cassette is not None

    @property
    def state(self) -> str:
        if not self._playing:
            return "stopped"
        return "paused" if self._paused else "playing"

    @property
    def volume(self) -> float:
        return self._volume

    @property
    def duration_ms(self) -> float:
        if self._cassette is None:
            return 0.0
        return self._cassette.duration_s * 1000.0

    @property
    def position_ms(self) -> float:
        return self._pos * 1000.0 / self.samplerate

    @property
    def has_audio_output(self) -> bool:
        return self._sink is not None

    # -- événements --------------------------------------------------------
    def _emit(self, kind: str, message: str = "") -> None:
        with self._events_lock:
            self._events.append((kind, message))

    def drain_events(self) -> list[tuple[str, str]]:
        with self._events_lock:
            events = list(self._events)
            self._events.clear()
        return events

    # -- chargement --------------------------------------------------------
    def load(self, cassette: Cassette) -> None:
        self.stop()
        with self._io_lock:
            self._cassette = cassette
            self._active = min(self._active, TRACK_COUNT - 1)
            self._set_target(self._active)
            self._pos = 0
            self._apply_position_event()

    def set_active_track(self, index: int, force: bool = False) -> None:
        """Change de tête de lecture sans déplacer la bande (comme le jouet)."""
        if not 0 <= index < TRACK_COUNT:
            return
        if not force and index == self._active:
            return
        self._active = index
        self._set_target(index)
        if not self._playing:
            self._apply_position_event()

    def _set_target(self, index: int) -> None:
        target = np.zeros(TRACK_COUNT, dtype=np.float32)
        target[index] = 1.0
        self._gain_target = target

    def _apply_position_event(self) -> None:
        self._emit("position", f"{self.position_ms:.0f}")

    # -- commandes de transport -------------------------------------------
    def play(self, position_ms: float | None = None) -> None:
        if self._cassette is None or not self._cassette.tracks:
            raise CassetteError("Aucune cassette chargée.")
        # Après un arrêt (y compris un changement de cassette), le flux
        # PortAudio est stoppé : sans ce redémarrage, plus aucun bloc n'est
        # calculé et la lecture reste silencieuse jusqu'au redémarrage de
        # l'application.
        self._ensure_sink()
        if self._sink is not None:
            with contextlib.suppress(Exception):
                self._sink.start()
            self.samplerate = int(self._sink.samplerate)
        start_ms = self.position_ms if position_ms is None else position_ms
        if start_ms >= self.duration_ms - 20:
            start_ms = 0.0
        start_ms = max(0.0, min(float(start_ms), self.duration_ms))
        self._commands.put(("play", start_ms))
        # État immédiat : l'interface affiche « Pause » sans attendre le
        # premier bloc audio. Le callback ouvrira les fichiers juste après.
        self._playing = True
        self._paused = False
        self._emit("state", "playing")

    def pause(self) -> None:
        if self._playing and not self._paused:
            self._paused = True
            self._emit("state", "paused")

    def resume(self) -> None:
        if self._playing and self._paused:
            self._paused = False
            self._emit("state", "playing")

    def toggle(self) -> None:
        if not self._playing:
            self.play()
        elif self._paused:
            self.resume()
        else:
            self.pause()

    def stop(self) -> None:
        with self._io_lock:
            self._stopping = True
            self._playing = False
            self._paused = False
            self._pos = 0
            self._gain_now = np.zeros(TRACK_COUNT, dtype=np.float32)
            self._close_readers()
            if self._sink is not None:
                self._sink.stop()
            self._stopping = False
        self._emit("state", "stopped")

    def seek(self, position_ms: float) -> None:
        target = max(0.0, min(float(position_ms), self.duration_ms))
        self._pos = int(round(target * self.samplerate / 1000.0))
        if self._playing:
            # Appliqué dans le callback, y compris en pause (silence) : au
            # retour de lecture, les quatre têtes repartent du bon endroit.
            self._commands.put(("seek", target))
        else:
            self._emit("position", f"{target:.0f}")

    def set_volume(self, value: float) -> None:
        self._volume = float(max(0.0, min(1.0, value)))

    # -- sortie ------------------------------------------------------------
    def _ensure_sink(self) -> None:
        if self._sink is not None:
            # Une sortie fermée par le système (changement de périphérique,
            # veille, erreur de pilote) est remplacée par une neuve.
            if getattr(self._sink, "is_running", True) or self._stream_ok:
                return
            self._discard_sink()
        with contextlib.suppress(Exception):
            self._sink = self._sink_factory(self._audio_callback, self._on_status_name)
            self.samplerate = int(self._sink.samplerate)
            self._stream_ok = True
            return
        # Périphérique indisponible : la lecture se fera en silence, sans
        # empêcher l'interface de fonctionner.
        self._sink = None
        self._stream_ok = False

    def _discard_sink(self) -> None:
        if self._sink is not None:
            with contextlib.suppress(Exception):
                self._sink.close()
            self._sink = None
        self._stream_ok = False

    def _on_status_name(self, message: str) -> None:
        self._emit("status", message)

    def _audio_callback(self, outdata: np.ndarray, frames: int) -> None:
        self._stream_ok = True
        with self._io_lock:
            block = self._process(frames)
        outdata[:] = block
        if frames == 0:
            return
        if self._playing and not self._paused:
            self._pos += frames
            end = int(self.duration_ms * self.samplerate / 1000.0)
            if self._pos >= end:
                self._pos = max(0, end)
                self._playing = False
                self._paused = False
                self._close_readers()
                self._emit("ended", "")

    # -- rendu -------------------------------------------------------------
    def _process(self, frames: int) -> np.ndarray:
        self._drain_commands()
        silence = np.zeros((frames, OUT_CHANNELS), dtype=np.float32)
        if not self._playing or self._paused or frames <= 0:
            return silence
        return self._render(frames)

    def _drain_commands(self) -> None:
        while True:
            try:
                kind, payload = self._commands.get_nowait()
            except queue.Empty:
                return
            if kind == "play":
                self._begin_play(float(payload))
            elif kind == "seek":
                self._seek_to(float(payload))

    def _begin_play(self, position_ms: float) -> None:
        cassette = self._cassette
        if cassette is None:
            return
        self._pos = int(round(position_ms * self.samplerate / 1000.0))
        try:
            self._open_readers(self._pos)
        except CassetteError as exc:
            self._emit("error", str(exc))
            self._playing = False
            return
        self._gain_now = np.zeros(TRACK_COUNT, dtype=np.float32)
        self._playing = True
        self._paused = False

    def _seek_to(self, position_ms: float) -> None:
        self._pos = int(round(position_ms * self.samplerate / 1000.0))
        for reader in self._readers:
            reader.seek(self._pos)
        self._gain_now = np.zeros(TRACK_COUNT, dtype=np.float32)
        self._emit("position", f"{position_ms:.0f}")

    def _open_readers(self, out_frame: int) -> None:
        self._close_readers()
        cassette = self._cassette
        if cassette is None:
            return
        readers: list[_TrackReader] = []
        track = None
        try:
            for track in cassette.tracks:
                reader = _TrackReader(track, self.samplerate)
                reader.open(out_frame)
                readers.append(reader)
        except Exception as exc:
            for reader in readers:
                reader.close()
            name = f" ({track.filename})" if track is not None else ""
            raise CassetteError(
                f"Lecture impossible{name} : {exc}. Vérifie que le fichier "
                "existe toujours et qu'il n'est pas ouvert ailleurs."
            ) from exc
        self._readers = readers

    def _close_readers(self) -> None:
        for reader in self._readers:
            reader.close()
        self._readers = []

    def _render(self, frames: int) -> np.ndarray:
        mix = np.zeros((frames, OUT_CHANNELS), dtype=np.float32)
        if not self._readers:
            return mix

        t = (np.arange(frames, dtype=np.float32) + 1.0) / max(frames, 1)
        gains = self._gain_now[:, None] + (self._gain_target - self._gain_now)[:, None] * t[None, :]
        self._gain_now = self._gain_target.copy()

        for index, reader in enumerate(self._readers[:TRACK_COUNT]):
            column = gains[index]
            if not np.any(column):
                # On avance quand même la piste : elle doit rester synchrone.
                reader.read(frames)
                continue
            mix += reader.read(frames) * column[:, None]

        if self._volume != 1.0:
            mix *= np.float32(self._volume)
        np.clip(mix, -1.0, 1.0, out=mix)
        return mix

    # -- rendu hors ligne (tests, mode --check) ---------------------------
    def render_offline(self, frames: int, start_ms: float = 0.0) -> np.ndarray:
        """Rend un bloc sans périphérique audio. Utilisé par les tests."""
        if self._cassette is None:
            raise CassetteError("Aucune cassette chargée.")
        with self._io_lock:
            self.samplerate = DEFAULT_RATE
            self._playing = True
            self._paused = False
            self._pos = int(round(start_ms * self.samplerate / 1000.0))
            self._open_readers(self._pos)
            # Gain immédiat : la sortie est alors exactement comparable au
            # fichier source (utile aux tests d'alignement).
            self._gain_target = np.zeros(TRACK_COUNT, dtype=np.float32)
            self._gain_target[self._active] = 1.0
            self._gain_now = self._gain_target.copy()
            return self._render(frames)

    # -- arrêt -------------------------------------------------------------
    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.stop()
        if self._sink is not None:
            self._sink.close()
            self._sink = None
