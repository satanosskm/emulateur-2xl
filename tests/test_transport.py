"""Tests de la machine à états (lecture, pause, seek, fin de bande).

Un « faux » périphérique pilote exactement le même callback que PortAudio,
ce qui rend les tests déterministes et indépendants de la carte son.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from twoxl.engine import AudioEngine, build_cassette

RATE = 44100
TONES = (220.0, 330.0, 440.0, 550.0)


class FakeSink:
    def __init__(self, callback, *_args, **_kwargs) -> None:
        self.callback = callback
        self.samplerate = RATE
        self.is_running = False

    def start(self) -> None:
        self.is_running = True

    def stop(self) -> None:
        self.is_running = False

    def close(self) -> None:
        self.is_running = False


def _write(path: Path, freq: float, duration: float = 3.0) -> Path:
    t = np.arange(int(RATE * duration)) / RATE
    sf.write(path, (0.5 * np.sin(2 * math.pi * freq * t)).astype(np.float32), RATE, subtype="PCM_16")
    return path


@pytest.fixture()
def engine(tmp_path: Path):
    paths = [_write(tmp_path / f"Track{i + 1}.wav", f) for i, f in enumerate(TONES)]
    cassette = build_cassette(paths, name="Transport")
    engine = AudioEngine(sink_factory=FakeSink)
    engine.load(cassette)
    yield engine
    engine.close()


def pump(engine: AudioEngine, blocks: int, frames: int = 1024) -> np.ndarray:
    last = np.zeros((frames, 2), dtype=np.float32)
    for _ in range(blocks):
        buffer = np.zeros((frames, 2), dtype=np.float32)
        engine._audio_callback(buffer, frames)
        last = buffer
    return last


def dominant(block: np.ndarray) -> float:
    mono = block[:, 0] * np.hanning(len(block))
    spectrum = np.abs(np.fft.rfft(mono))
    return int(np.argmax(spectrum)) * RATE / len(block)


def test_position_advances_in_real_time(engine) -> None:
    engine.play()
    pump(engine, 43)  # ~1 s
    assert engine.state == "playing"
    assert engine.position_ms == pytest.approx(1000, abs=60)


def test_pause_freezes_position(engine) -> None:
    engine.play()
    pump(engine, 10)
    engine.pause()
    frozen = engine.position_ms
    pump(engine, 20)
    assert engine.position_ms == pytest.approx(frozen, abs=2)
    engine.resume()
    pump(engine, 5)
    assert engine.position_ms > frozen


def test_switch_track_keeps_position(engine) -> None:
    engine.play()
    pump(engine, 20)
    before = engine.position_ms
    engine.set_active_track(2)
    pump(engine, 2)  # laisse la rampe de commutation se terminer
    collected = np.concatenate([pump(engine, 1) for _ in range(64)])  # ~1,5 s
    expected = before + (2 + 64) * 1024 * 1000 / RATE
    assert engine.position_ms == pytest.approx(expected, abs=60)
    assert dominant(collected) == pytest.approx(TONES[2], rel=0.01)


def test_seek_while_playing(engine) -> None:
    engine.play()
    pump(engine, 5)
    engine.seek(2000)
    pump(engine, 2)
    assert engine.position_ms == pytest.approx(2000, abs=60)


def test_seek_while_paused_then_resume(engine) -> None:
    engine.play()
    pump(engine, 5)
    engine.pause()
    engine.seek(1500)
    assert engine.state == "paused"
    engine.resume()
    pump(engine, 1)
    assert engine.position_ms == pytest.approx(1500 + 1024 * 1000 / RATE, abs=60)


def test_end_of_tape_stops_engine(engine) -> None:
    duration = engine.duration_ms
    engine.play(max(0.0, duration - 40))
    pump(engine, 4)
    assert engine.state == "stopped"
    kinds = [kind for kind, _ in engine.drain_events()]
    assert "ended" in kinds


def test_volume_is_applied(engine) -> None:
    engine.set_volume(0.0)
    engine.play()
    block = pump(engine, 8)
    assert np.max(np.abs(block)) < 1e-6
    assert engine.volume == 0.0


def test_stop_resets_position(engine) -> None:
    engine.play()
    pump(engine, 12)
    engine.stop()
    assert engine.position_ms == 0
    assert engine.state == "stopped"


def test_play_before_load_fails() -> None:
    from twoxl.engine import CassetteError

    with pytest.raises(CassetteError):
        AudioEngine(sink_factory=FakeSink).play()
