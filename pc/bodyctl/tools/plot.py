"""The "boxer tell" graphic: vertical acceleration around an event, with the dip,
the moment the belt node fires, and take-off / landing marked (video chapter 3)."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from bodyctl.trace import Trace


def vertical_g(trace: Trace, node_id: int | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Time (s) and dynamic acceleration |a| - 1 g, independent of how the node is mounted."""
    t, a = [], []
    lsb = trace.header.get("imu", {}).get("accel_lsb_g", 0.000244)
    for _, ts, s in trace.raw_samples(node_id):
        t.append(ts)
        a.append(np.linalg.norm(s.accel) * lsb - 1.0)
    return np.array(t) / 1e6, np.array(a)


def plot_event(
    trace_path: str | Path,
    out: str | Path,
    fired_s: list[float],
    event: str = "jump",
    before_s: float = 0.6,
    after_s: float = 1.0,
    title: str | None = None,
) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    tr = Trace.read(trace_path)
    t, a = vertical_g(tr)
    labels = {lb.event: lb.t_us / 1e6 for lb in tr.device_labels()}
    anchor = fired_s[0] if fired_s else labels.get(event, t[len(t) // 2])
    sel = (t >= anchor - before_s) & (t <= anchor + after_s)
    x = (t[sel] - anchor) * 1000  # ms from the event
    fig, ax = plt.subplots(figsize=(9, 4.2))
    ax.plot(x, a[sel], color="#2f6fdf", linewidth=1.6)
    ax.axhline(0, color="#999", linewidth=0.8)
    ax.fill_between(x, a[sel], 0, where=a[sel] < 0, color="#2f6fdf", alpha=0.12)

    def mark(t_s: float, text: str, color: str, y: float) -> None:
        xm = (t_s - anchor) * 1000
        ax.axvline(xm, color=color, linewidth=1.4, linestyle="--")
        ax.annotate(text, (xm, y), xytext=(6, 0), textcoords="offset points", color=color, fontsize=10)

    if "jump_onset" in labels:
        mark(labels["jump_onset"], "dip starts (the tell)", "#7a7a7a", 1.6)
    for f in fired_s:
        mark(f, f"{event.upper()} fires", "#d8473b", 2.2)
    if "takeoff" in labels:
        mark(labels["takeoff"], "feet leave the ground", "#3a9a5b", 1.0)
    if "landing" in labels:
        mark(labels["landing"], "landing", "#7a7a7a", 2.6)
    if fired_s and "takeoff" in labels:
        lead = (labels["takeoff"] - fired_s[0]) * 1000
        ax.set_title(title or f"The node fires {lead:.0f} ms before your feet leave the ground", fontsize=12)
    elif title:
        ax.set_title(title, fontsize=12)
    ax.set_xlabel("ms")
    ax.set_ylabel("vertical acceleration (g, gravity removed)")
    ax.set_ylim(-1.3, 3.2)
    fig.tight_layout()
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=160)
    plt.close(fig)
    return out
