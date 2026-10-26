import pytest

from bodyctl.config.profile import load_profile
from bodyctl.protocol import Command
from bodyctl.tools.hil import run_hil, save_results


class FakeLink:
    def __init__(self):
        self.calls = []

    def command_values(self, cmd, v0, v1, timeout=1.0):
        self.calls.append((cmd, v0, v1))


def test_hil_walks_every_port_and_always_ends(tmp_path):
    link = FakeLink()
    prof = load_profile("config/profiles/gta5.yaml")
    questions = []

    def ask(q):
        questions.append(q)
        return "B-E1" not in q  # pretend the right stick is wired wrong

    results = run_hil(link, prof, ask, hold_s=0)
    # 2 sticks x 5 checks + 2 triggers x 2 + 4 buttons
    assert len(results) == 2 * 5 + 2 * 2 + 4
    assert all(c[0] == Command.HIL for c in link.calls)
    assert link.calls[-1] == (Command.HIL, -1, 0)  # bench mode always switched off
    assert (Command.HIL, 2, 1) in link.calls  # A-E3 square pressed
    assert (Command.HIL, 5, 1000) in link.calls  # B-E2 R2 full pull
    assert sum(not r["ok"] for r in results) == 5
    path = save_results(results, tmp_path)
    assert path.exists()


def test_hil_ends_bench_mode_on_interrupt():
    link = FakeLink()

    def ask(q):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_hil(link, load_profile("config/profiles/gta5.yaml"), ask, hold_s=0)
    assert link.calls[-1] == (Command.HIL, -1, 0)
