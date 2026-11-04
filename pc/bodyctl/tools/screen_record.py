"""Record a game window to video with a capture time per frame, on the same clock as the
drills (perf_counter, shared across processes), so the game's reaction lines up with each
cue and each button the body pressed.

Writes <out>.mp4 and <out>.t_us.npy (capture time of every frame, µs). Windows finds the
window by title; elsewhere pass --region.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

from bodyctl.sensors.webcam_pose import now_us


def window_rect(title: str) -> tuple[int, int, int, int]:
    """(left, top, right, bottom) in screen pixels of the first window whose title contains
    `title` (Windows only)."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        n = user32.GetWindowTextLengthW(hwnd)
        if n and user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if title.lower() in buf.value.lower():
                found.append(hwnd)
                return False
        return True

    user32.EnumWindows(visit, 0)
    if not found:
        raise RuntimeError(f"no window titled like {title!r}")
    r = wintypes.RECT()
    user32.GetWindowRect(found[0], ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


class _GdiGrabber:
    """Screen region scaled down while copying (StretchBlt): far faster than grabbing a
    large window at full size and resizing it in Python."""

    def __init__(self, box: tuple[int, int, int, int], size: tuple[int, int]):
        import ctypes
        from ctypes import wintypes

        self._c, self._w = ctypes, wintypes
        self.u32, self.g32 = ctypes.windll.user32, ctypes.windll.gdi32
        self.box, self.size = box, size
        self.screen = self.u32.GetDC(0)
        self.mem = self.g32.CreateCompatibleDC(self.screen)
        self.bmp = self.g32.CreateCompatibleBitmap(self.screen, size[0], size[1])
        self.g32.SelectObject(self.mem, self.bmp)
        self.g32.SetStretchBltMode(self.mem, 4)  # HALFTONE: smooth downscale

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                        ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                        ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                        ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]  # fmt: skip

        self.info = BITMAPINFOHEADER()
        self.info.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        self.info.biWidth, self.info.biHeight = size[0], -size[1]  # top-down rows
        self.info.biPlanes, self.info.biBitCount = 1, 32
        self.buf = np.empty((size[1], size[0], 4), np.uint8)

    def grab(self) -> np.ndarray:
        x0, y0, x1, y1 = self.box
        self.g32.StretchBlt(
            self.mem, 0, 0, self.size[0], self.size[1], self.screen, x0, y0, x1 - x0, y1 - y0, 0x00CC0020
        )
        self.g32.GetDIBits(
            self.mem, self.bmp, 0, self.size[1], self.buf.ctypes.data_as(self._c.c_void_p),
            self._c.byref(self.info), 0,
        )  # fmt: skip
        return self.buf[:, :, :3]

    def close(self) -> None:
        self.g32.DeleteObject(self.bmp)
        self.g32.DeleteDC(self.mem)
        self.u32.ReleaseDC(0, self.screen)


def record(
    out: str | Path,
    title: str | None = None,
    region: tuple[int, int, int, int] | None = None,
    seconds: float = 300.0,
    fps: float = 20.0,
    width: int = 1280,
    stop_file: str | Path | None = None,
) -> Path:
    import cv2

    if sys.platform == "win32":
        import contextlib
        import ctypes

        with contextlib.suppress(Exception):
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # real pixels on scaled displays
    box = region or window_rect(title or "Grand Theft Auto V")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    w0, h0 = box[2] - box[0], box[3] - box[1]
    size = (width, round(h0 * width / w0) // 2 * 2)
    writer = cv2.VideoWriter(str(out.with_suffix(".mp4")), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
    stamps: list[int] = []
    t_end = time.monotonic() + seconds
    period = 1 / fps
    print(
        f"recording {box} -> {out.with_suffix('.mp4')} at {fps:.0f} fps for up to {seconds:.0f} s", flush=True
    )
    if sys.platform == "win32":
        grabber = _GdiGrabber(box, size)
        grab = grabber.grab
    else:
        from PIL import ImageGrab

        grabber = None

        def grab() -> np.ndarray:
            frame = np.asarray(ImageGrab.grab(bbox=box, all_screens=True))[:, :, ::-1]
            return cv2.resize(frame, size, interpolation=cv2.INTER_AREA)

    try:
        while time.monotonic() < t_end and not (stop_file and Path(stop_file).exists()):
            t0 = time.monotonic()
            t_us = now_us()
            writer.write(np.ascontiguousarray(grab()))
            stamps.append(t_us)
            time.sleep(max(0.0, period - (time.monotonic() - t0)))
    except KeyboardInterrupt:
        pass
    finally:
        writer.release()
        if grabber is not None:
            grabber.close()
        np.save(out.with_suffix(".t_us.npy"), np.array(stamps, np.int64))
    span = (stamps[-1] - stamps[0]) / 1e6 if len(stamps) > 1 else 0.0
    print(f"saved {len(stamps)} frames ({len(stamps) / max(span, 1e-6):.1f} fps real)", flush=True)
    return out.with_suffix(".mp4")
