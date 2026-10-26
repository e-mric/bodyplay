import copy
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from bodyctl.config.profile import Profile, load_profile

GTA5 = Path(__file__).parents[2] / "config" / "profiles" / "gta5.yaml"


@pytest.fixture
def gta5_data() -> dict:
    return yaml.safe_load(GTA5.read_text())


def test_gta5_validates():
    prof = load_profile(GTA5)
    assert prof.profile == "gta5"
    assert prof.gestures.jump.dip_min_ms == 40


def test_gta5_port_table_unchanged():
    # The PS5 Access controller profile is set up from this table; keep it stable.
    prof = load_profile(GTA5)
    table = {k: (v.type.value, v.ps5) for k, v in prof.ports.items()}
    assert table == {
        "A-E1": ("stick", "left_stick"),
        "A-E2": ("button", "cross"),
        "A-E3": ("button", "square"),
        "A-E4": ("button", "triangle"),
        "B-E1": ("stick", "right_stick"),
        "B-E2": ("trigger", "r2"),
        "B-E3": ("trigger", "l2"),
        "B-E4": ("button", "circle"),
    }


def test_mode_lookup_and_neutral_default():
    prof = load_profile(GTA5)
    assert prof.mode_for("boat") is prof.modes["car"]
    assert prof.mode_for("plane") is prof.modes["heli"]
    assert prof.binding("on_foot", "A-E3").event == "jump"
    assert prof.binding("car", "A-E1").kind == "neutral"
    assert prof.binding("qte", "A-E3").kind == "neutral"  # unlisted = released
    assert prof.binding("paused", "B-E2").kind == "neutral"


def test_defaults_fill_missing_gestures(gta5_data):
    del gta5_data["gestures"]
    prof = Profile.model_validate(gta5_data)
    assert prof.gestures.step.refractory_ms == 200


def _invalid(data: dict, match: str) -> None:
    with pytest.raises(ValidationError, match=match):
        Profile.model_validate(data)


def test_press_timing_rule(gta5_data):
    gta5_data["press"]["hold_ms"] = 20
    _invalid(gta5_data, "hold_ms")


def test_port_type_must_match_ps5_output(gta5_data):
    gta5_data["ports"]["A-E3"] = {"type": "stick", "ps5": "square"}
    _invalid(gta5_data, "can't emit")


def test_vehicle_sticks_neutral(gta5_data):
    gta5_data["modes"]["car"]["ports"]["A-E1"] = {"y": "run_cadence"}
    _invalid(gta5_data, "neutral in vehicle")


def test_shoot_requires_aiming(gta5_data):
    gta5_data["modes"]["on_foot"]["ports"]["B-E2"] = {"event": "shoot"}
    _invalid(gta5_data, "require aiming")


def test_vehicle_exit_needs_gate(gta5_data):
    gta5_data["modes"]["car"]["ports"]["A-E4"] = {"event": "enter_exit"}
    _invalid(gta5_data, "state gate")


def test_costly_confidence_floor(gta5_data):
    gta5_data["gestures"]["shoot"]["min_conf"] = 0.5
    _invalid(gta5_data, "min_conf")


def test_binding_kind_must_fit_port(gta5_data):
    gta5_data["modes"]["on_foot"]["ports"]["A-E1"] = {"event": "jump"}
    _invalid(gta5_data, "can't take a event")


def test_one_binding_kind(gta5_data):
    gta5_data["modes"]["on_foot"]["ports"]["A-E3"] = {"event": "jump", "pulse": "step_rhythm"}
    _invalid(gta5_data, "one of")


def test_unknown_names_rejected(gta5_data):
    bad = copy.deepcopy(gta5_data)
    bad["modes"]["on_foot"]["ports"]["A-E3"] = {"event": "jmup"}
    _invalid(bad, "event")
    bad = copy.deepcopy(gta5_data)
    bad["modes"]["hovercraft"] = {}
    _invalid(bad, "hovercraft")
    bad = copy.deepcopy(gta5_data)
    bad["gestures"]["jump"]["dip_gg"] = 1
    _invalid(bad, "dip_gg")


def test_mode_covered_once(gta5_data):
    gta5_data["modes"]["heli"]["also"].append("boat")
    _invalid(gta5_data, "defined by both")


def test_unknown_port_in_mode(gta5_data):
    gta5_data["modes"]["on_foot"]["ports"]["C-E1"] = {"event": "jump"}
    _invalid(gta5_data, "C-E1")
