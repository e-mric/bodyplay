"""Spoken drill cues, for drills done away from the screen (pull-ups at the bar).

One short clip per cue, recorded once with ElevenLabs (voice "Coach Cap", model
eleven_v4) and shipped as 22 kHz mono WAV in `voice/`, so a drill needs no network
or API key. `LINES` is what each clip says: to change one, generate the new line,
trim its silences and save it as `voice/<key>.wav`.
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

CLIP_DIR = Path(__file__).parent / "voice"

LINES = {
    "count_3": "Three.",
    "count_2": "Two.",
    "count_1": "One.",
    "still": "Stand still, arms down.",
    "shift": "Move around naturally, no actions.",
    "done": "Done, nice work!",
    "ready_pullup": "Next up, pull-ups.",
    "ready_pushup": "Next up, push-ups.",
    "ready_kettlebell": "Next up, kettlebell swings.",
    "ready_squat": "Next up, squats.",
    "ready_boxing": "Next up, boxing.",
    "hang": "Hang from the bar, arms straight.",
    "pullup": "Pull up!",
    "drop": "Drop down and stand.",
    "plank": "Get down in a plank.",
    "pushup": "Push up!",
    "get_up": "Stand up.",
    "kb_ready": "Grab the kettlebell.",
    "swing": "Swing!",
    "put_down": "Put it down.",
    "rack_squat": "Stand tall, hands at your shoulders.",
    "squat": "Squat!",
    "rack_down": "Lower your hands.",
    "boxing_ready": "Guard up.",
    "jab": "Jab!",
    "cross": "Cross!",
}


def load_clip(path: Path) -> tuple[np.ndarray, int]:
    """A 16-bit mono WAV as float32 samples and its rate."""
    with wave.open(str(path)) as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1:
            raise ValueError(f"{path}: expected 16-bit mono")
        data = np.frombuffer(w.readframes(w.getnframes()), np.int16)
        return data.astype(np.float32) / 32768.0, w.getframerate()


class Sound:
    """The drill's audio: a beep, and the spoken cues when `voice` is on. A new sound
    cuts off the one playing. Silently off when there's no audio device."""

    def __init__(self, enabled: bool, voice: bool = True, clip_dir: Path = CLIP_DIR):
        self._sd = None
        self._clips: dict[str, tuple[np.ndarray, int]] = {}
        if not enabled:
            return
        try:
            import sounddevice as sd
        except Exception:
            return
        self._sd = sd
        self._beep_sr = 44100
        t = np.arange(int(0.12 * self._beep_sr)) / self._beep_sr
        self._beep = (0.3 * np.sin(2 * np.pi * 880 * t)).astype(np.float32)
        if voice:
            for key in LINES:
                path = clip_dir / f"{key}.wav"
                if path.exists():
                    self._clips[key] = load_clip(path)

    def beep(self) -> None:
        self._play(self._beep, self._beep_sr) if self._sd is not None else None

    def say(self, key: str) -> bool:
        """Speak the cue; False when there's no clip for it (or no audio)."""
        clip = self._clips.get(key)
        return clip is not None and self._play(*clip)

    def _play(self, samples: np.ndarray, rate: int) -> bool:
        if self._sd is None:
            return False
        try:
            self._sd.play(samples, rate)
            return True
        except Exception:
            self._sd = None
            return False
