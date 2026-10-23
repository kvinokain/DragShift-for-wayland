"""Linux integration on Wayland (niri, Hyprland, Sway, KDE, GNOME…).

* Trigger: Wayland gives no app the global button/modifier state that X11's XQueryPointer
  does, so the input devices are read directly with python-evdev (the user must be in the
  `input` group). Same interface as platform_linux / platform_mac: left_button_down /
  key_down / escape_down — plus pointer_pos(), because QCursor.pos() is stale on Wayland.
  pointer_pos() is a running sum of relative motion, not a screen position; the trigger
  only needs distances travelled.
* Touchpads: a press-and-hold drag (physical click) is seen; tap-and-drag is synthesised
  by libinput above evdev and is invisible here.
* The menu itself is a layer-shell overlay, see layershell.py.
"""
from __future__ import annotations

import os
import select
import threading
import time

try:
    import evdev
    from evdev import ecodes as e
except ImportError:  # pragma: no cover
    evdev = None

TOUCHPAD_PX_PER_MM = 4.0     # rough libinput-like speed, only used for the drag threshold


def is_wayland() -> bool:
    return os.environ.get("XDG_SESSION_TYPE") == "wayland" or bool(os.environ.get("WAYLAND_DISPLAY"))


class _Touchpad:
    def __init__(self, dev):
        rx = dev.absinfo(e.ABS_X).resolution or 10
        ry = dev.absinfo(e.ABS_Y).resolution or 10
        self.kx, self.ky = TOUCHPAD_PX_PER_MM / rx, TOUCHPAD_PX_PER_MM / ry
        self.last: list[int | None] = [None, None]


class _Input(threading.Thread):
    """Reads every keyboard / pointer device and keeps the pressed keys and pointer travel."""

    def __init__(self):
        super().__init__(name="evdev-input", daemon=True)
        self.down: set[int] = set()
        self.x = self.y = 0.0
        self.devs: dict[str, "evdev.InputDevice"] = {}
        self.pads: dict[str, _Touchpad] = {}
        self.denied = False
        self._scan()

    def _scan(self):
        wanted = {e.BTN_LEFT, e.KEY_LEFTSHIFT, e.KEY_ESC}
        for path in evdev.list_devices():
            if path in self.devs:
                continue
            try:
                dev = evdev.InputDevice(path)
            except PermissionError:
                self.denied = True
                continue
            except OSError:
                continue
            caps = dev.capabilities()
            if not wanted & set(caps.get(e.EV_KEY, [])):
                dev.close()
                continue
            self.devs[path] = dev
            if e.ABS_X in dict(caps.get(e.EV_ABS, [])) and e.BTN_TOUCH in caps.get(e.EV_KEY, []):
                self.pads[path] = _Touchpad(dev)

    def run(self):
        last_scan = time.monotonic()
        while True:
            if time.monotonic() - last_scan > 3:       # hot-plugged mouse / keyboard
                last_scan = time.monotonic()
                self._scan()
            try:
                ready, _, _ = select.select(list(self.devs.values()), [], [], 1.0)
            except (OSError, ValueError):
                ready = []
            for dev in ready:
                try:
                    events = list(dev.read())
                except OSError:                        # unplugged: forget what it held
                    self.devs.pop(dev.path, None)
                    self.pads.pop(dev.path, None)
                    self.down.clear()
                    continue
                pad = self.pads.get(dev.path)
                for ev in events:
                    self._feed(ev, pad)

    def _feed(self, ev, pad: _Touchpad | None):
        if ev.type == e.EV_KEY:
            if ev.value:
                self.down.add(ev.code)
            else:
                self.down.discard(ev.code)
            if pad and ev.code == e.BTN_TOUCH and not ev.value:
                pad.last = [None, None]
        elif ev.type == e.EV_REL:
            if ev.code == e.REL_X:
                self.x += ev.value
            elif ev.code == e.REL_Y:
                self.y += ev.value
        elif ev.type == e.EV_ABS and pad and ev.code in (e.ABS_X, e.ABS_Y):
            i = 0 if ev.code == e.ABS_X else 1
            if pad.last[i] is not None and e.BTN_TOUCH in self.down:
                d = ev.value - pad.last[i]
                if i == 0:
                    self.x += d * pad.kx
                else:
                    self.y += d * pad.ky
            pad.last[i] = ev.value


_IN: _Input | None = None


def _input() -> _Input | None:
    global _IN
    if _IN is None and evdev is not None and is_wayland():
        _IN = _Input()
        if _IN.devs:
            _IN.start()
        elif _IN.denied:
            print("dragshift: Wayland trigger needs read access to /dev/input — "
                  "add yourself to the 'input' group and log in again:  sudo usermod -aG input $USER",
                  flush=True)
    return _IN


def available() -> bool:
    i = _input()
    return bool(i and i.devs)


# ---------------------------------------------------------------- input state
KEY_CODES = {}
if evdev is not None:
    KEY_CODES = {"shift": (e.KEY_LEFTSHIFT, e.KEY_RIGHTSHIFT),
                 "ctrl": (e.KEY_LEFTCTRL, e.KEY_RIGHTCTRL),
                 "alt": (e.KEY_LEFTALT, e.KEY_RIGHTALT)}


def left_button_down() -> bool:
    return e.BTN_LEFT in _IN.down


def key_down(name: str) -> bool:
    return any(k in _IN.down for k in KEY_CODES.get(name, KEY_CODES["shift"]))


def escape_down() -> bool:
    return e.KEY_ESC in _IN.down


def pointer_pos() -> tuple[int, int]:
    return int(_IN.x), int(_IN.y)
