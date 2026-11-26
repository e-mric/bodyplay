"""Spoken drill cues: every workout cue has a clip that starts at once and fits its step."""

import numpy as np

from bodyctl.tools.drill import WORKOUT_MOVES, build_script
from bodyctl.tools.voice import CLIP_DIR, LINES, Sound, load_clip


def test_every_line_has_a_clip_that_starts_on_the_cue():
    for key in LINES:
        samples, rate = load_clip(CLIP_DIR / f"{key}.wav")
        assert 0.25 < len(samples) / rate < 2.5, key
        # Speech within 60 ms of the start: the cue time is when you hear it.
        loud = np.flatnonzero(np.abs(samples) > 0.02 * np.abs(samples).max())
        assert loud[0] / rate < 0.06, key


def test_workout_drills_are_spoken_from_start_to_end():
    for move in WORKOUT_MOVES:
        for step in build_script([move], 3):
            key = step.voice or step.label
            assert key in LINES, (move, key)
            samples, rate = load_clip(CLIP_DIR / f"{key}.wav")
            assert len(samples) / rate < step.seconds, (move, key)  # said before the next cue


def test_sound_off_says_nothing():
    quiet = Sound(enabled=False)
    assert not quiet.say("pullup")
    quiet.beep()  # no audio device needed
