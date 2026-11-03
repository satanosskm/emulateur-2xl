"""Tests du moteur, de la détection de cassettes et de la configuration."""

from __future__ import annotations

import json
import math
import time
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from twoxl.engine import (
    AudioEngine,
    CassetteError,
    build_cassette,
    inspect_track,
)
from twoxl.library import (
    AppConfig,
    _sanitize,
    cassette_from_paths,
    cassette_record,
    detect_track_paths,
    load_config,
    save_config,
)

RATE = 44100
DURATION = 2.0
TONES = (220.0, 330.0, 440.0, 550.0)


def _write_tone(path: Path, freq: float, *, rate: int = RATE, duration: float = DURATION, channels: int = 1) -> Path:
    t = np.arange(int(rate * duration)) / rate
    wave = 0.5 * np.sin(2 * math.pi * freq * t).astype(np.float32)
    if channels == 2:
        wave = np.stack([wave, wave * 0.5], axis=1)
    sf.write(path, wave, rate, subtype="PCM_16")
    return path


@pytest.fixture()
def cassette(tmp_path: Path):
    paths = [_write_tone(tmp_path / f"Track{i + 1}.wav", f) for i, f in enumerate(TONES)]
    return build_cassette(paths, name="Test")


def _dominant_frequency(block: np.ndarray, rate: int = RATE) -> float:
    mono = block[:, 0]
    spectrum = np.abs(np.fft.rfft(mono * np.hanning(len(mono))))
    index = int(np.argmax(spectrum))
    return index * rate / len(mono)


def test_dominant_tone_of_active_track(cassette) -> None:
    engine = AudioEngine()
    engine.load(cassette)
    for index, tone in enumerate(TONES):
        engine.set_active_track(index)
        block = engine.render_offline(16384)
        assert _dominant_frequency(block) == pytest.approx(tone, rel=0.01)


def test_track_switch_does_not_move_the_tape(cassette) -> None:
    engine = AudioEngine()
    engine.load(cassette)
    block = engine.render_offline(16384, start_ms=700)
    assert _dominant_frequency(block) == pytest.approx(TONES[0], rel=0.01)
    position_before = engine.position_ms
    engine.set_active_track(2)
    block = engine.render_offline(16384, start_ms=engine.position_ms)
    assert engine.position_ms == pytest.approx(position_before, abs=1.0)
    assert _dominant_frequency(block) == pytest.approx(TONES[2], rel=0.01)


def test_seek_keeps_four_tracks_aligned(cassette) -> None:
    engine = AudioEngine()
    engine.load(cassette)
    engine.render_offline(1024, start_ms=0)
    engine._playing = True  # pas de sortie audio dans les tests
    engine.seek(1500)
    # Le seek est appliqué au prochain bloc rendu, comme dans le callback audio.
    engine._drain_commands()
    assert engine.position_ms == pytest.approx(1500, abs=1)
    engine.set_active_track(3)
    engine._gain_now = engine._gain_target.copy()
    block = engine._render(16384)
    assert _dominant_frequency(block) == pytest.approx(TONES[3], rel=0.01)


def test_full_tape_duration_and_end(cassette) -> None:
    engine = AudioEngine()
    engine.load(cassette)
    assert engine.duration_ms == pytest.approx(DURATION * 1000, rel=0.01)
    engine.seek(engine.duration_ms - 10)
    assert engine.position_ms == pytest.approx(DURATION * 1000 - 10, abs=2)


def test_resampling_from_other_rate(tmp_path: Path) -> None:
    paths = [
        _write_tone(tmp_path / f"Piste{i + 1}.wav", f, rate=22050, duration=1.0)
        for i, f in enumerate(TONES)
    ]
    cassette = build_cassette(paths, name="22050")
    engine = AudioEngine()
    engine.load(cassette)
    block = engine.render_offline(16384)
    assert block.shape == (16384, 2)
    assert _dominant_frequency(block) == pytest.approx(TONES[0], rel=0.01)
    assert engine.duration_ms == pytest.approx(1000, rel=0.01)


def test_stereo_files_are_accepted(tmp_path: Path) -> None:
    paths = [
        _write_tone(tmp_path / f"Track{i + 1}.wav", f, channels=2)
        for i, f in enumerate(TONES)
    ]
    cassette = build_cassette(paths, name="Stéréo")
    engine = AudioEngine()
    engine.load(cassette)
    block = engine.render_offline(2048)
    assert block.shape[1] == 2
    assert np.max(np.abs(block)) > 0.05


def test_warning_when_durations_differ(tmp_path: Path) -> None:
    paths = [
        _write_tone(tmp_path / f"Track{i + 1}.wav", TONES[i], duration=DURATION if i else 0.4)
        for i in range(4)
    ]
    cassette = build_cassette(paths, name="Inégale")
    warnings = " ".join(cassette.warnings())
    assert "même durée" in warnings


def test_missing_file_is_reported(tmp_path: Path) -> None:
    with pytest.raises(CassetteError):
        inspect_track(tmp_path / "absent.mp3", 0, "OUI")


def test_build_requires_four_tracks(tmp_path: Path) -> None:
    path = _write_tone(tmp_path / "seul.wav", 220)
    with pytest.raises(CassetteError):
        build_cassette([path], "Incomplète")


def test_detect_track_paths_numbered(tmp_path: Path) -> None:
    for i in range(4):
        _write_tone(tmp_path / f"Track{i + 1}.mp3.wav", TONES[i])
    found = detect_track_paths(tmp_path)
    assert [p.name for p in found] == [f"Track{i + 1}.mp3.wav" for i in range(4)]


def test_detect_track_paths_labelled(tmp_path: Path) -> None:
    for label, tone in zip(("oui", "non", "vrai", "faux"), TONES, strict=True):
        _write_tone(tmp_path / f"{label}.wav", tone)
    found = detect_track_paths(tmp_path)
    assert [p.stem for p in found] == ["oui", "non", "vrai", "faux"]


def test_detect_track_paths_fallback(tmp_path: Path) -> None:
    for name in ("a.wav", "b.wav", "c.wav", "d.wav"):
        _write_tone(tmp_path / name, 220)
    assert len(detect_track_paths(tmp_path)) == 4


def test_detect_track_paths_rejects_incomplete(tmp_path: Path) -> None:
    for name in ("a.wav", "b.wav", "c.wav"):
        _write_tone(tmp_path / name, 220)
    with pytest.raises(CassetteError):
        detect_track_paths(tmp_path)


def test_cassette_from_paths_record_roundtrip(cassette) -> None:
    record = cassette_record(cassette)
    restored = cassette_from_paths(record["tracks"], record["name"], None)
    assert restored.duration_s == pytest.approx(cassette.duration_s)


def test_config_sanitises_bad_values() -> None:
    config = _sanitize({"volume": "2.5", "last_track": "99", "recent": "nope"})
    assert config.volume == 1.0
    assert config.last_track == 3
    assert config.recent == []


def test_config_roundtrip(tmp_path: Path) -> None:
    config = AppConfig(volume=0.42, last_track=2, path=tmp_path / "config.json")
    config.recent = [{"name": "X", "folder": None, "tracks": ["a", "b", "c", "d"]}]
    saved = save_config(config)
    assert saved is not None and saved.is_file()
    payload = json.loads(saved.read_text(encoding="utf-8"))
    assert payload["volume"] == pytest.approx(0.42)
    assert payload["recent"][0]["name"] == "X"


def test_corrupt_config_is_quarantined(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "config_2xl.json"
    target.write_text("{pas du json", encoding="utf-8")
    monkeypatch.setattr("twoxl.library.config_candidates", lambda: [target])
    config = load_config()
    assert config.warning
    assert (tmp_path / "config_2xl.json.bak").is_file()


def test_memory_stays_flat(cassette) -> None:
    """Le rendu de plusieurs minutes ne doit rien accumuler en mémoire."""
    engine = AudioEngine()
    engine.load(cassette)
    engine.render_offline(4096, start_ms=0)
    before = _rss_kb()
    for _ in range(400):  # ~9,3 s d'audio
        engine._render(1024)
    after = _rss_kb()
    assert after - before < 40_000, f"croissance mémoire de {after - before} Kio"


def _rss_kb() -> int:
    try:
        with open("/proc/self/status", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except OSError:
        return 0
    return 0


def test_render_speed(cassette) -> None:
    """Le décodage doit être bien plus rapide que le temps réel."""
    engine = AudioEngine()
    engine.load(cassette)
    start = time.perf_counter()
    engine.render_offline(4096, start_ms=0)
    engine._render(1024 * 64)  # ~1,5 s d'audio
    elapsed = time.perf_counter() - start
    assert elapsed < 1.5
