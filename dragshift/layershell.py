"""Wayland: turn a Qt window into a wlr-layer-shell surface (an overlay above all windows).

A plain Qt window on Wayland can't be placed under the cursor or kept above everything,
and the compositor won't route an in-progress drag to a surface that appears mid-drag.
So the radial menu becomes one full-screen layer surface that lives for the whole session
and only changes its input region: a single pixel while parked, the whole screen while open.

layer-shell-qt has no Python bindings, so its C++ API is called through ctypes. The methods
used here are non-virtual, take `this` as the first argument, and their enums / QFlags are
passed as plain ints, which makes the mangled-name calls stable across 6.x releases.
"""
from __future__ import annotations

import ctypes
import os

LAYER_OVERLAY = 3
ANCHOR_TOP, ANCHOR_BOTTOM, ANCHOR_LEFT, ANCHOR_RIGHT = 1, 2, 4, 8
ANCHOR_ALL = ANCHOR_TOP | ANCHOR_BOTTOM | ANCHOR_LEFT | ANCHOR_RIGHT
KBD_NONE, KBD_EXCLUSIVE, KBD_ON_DEMAND = 0, 1, 2

_SYMBOLS = {
    "get": "_ZN12LayerShellQt6Window3getEP7QWindow",
    "layer": "_ZN12LayerShellQt6Window8setLayerENS0_5LayerE",
    "anchors": "_ZN12LayerShellQt6Window10setAnchorsE6QFlagsINS0_6AnchorEE",
    "zone": "_ZN12LayerShellQt6Window16setExclusiveZoneEi",
    "keyboard": "_ZN12LayerShellQt6Window24setKeyboardInteractivityENS0_21KeyboardInteractivityE",
    "margins": "_ZN12LayerShellQt6Window10setMarginsERK8QMargins",
}


class _QMargins(ctypes.Structure):
    _fields_ = [("left", ctypes.c_int), ("top", ctypes.c_int),
                ("right", ctypes.c_int), ("bottom", ctypes.c_int)]
_lib = None


def is_wayland() -> bool:
    return os.environ.get("XDG_SESSION_TYPE") == "wayland" or bool(os.environ.get("WAYLAND_DISPLAY"))


def _load():
    global _lib
    if _lib is None:
        _lib = False
        try:
            lib = ctypes.CDLL("libLayerShellQtInterface.so.6")
            fns = {k: getattr(lib, sym) for k, sym in _SYMBOLS.items()}
        except (OSError, AttributeError):
            return False
        P, I = ctypes.c_void_p, ctypes.c_int
        fns["get"].argtypes, fns["get"].restype = [P], P
        for k in ("layer", "anchors", "zone", "keyboard"):
            fns[k].argtypes, fns[k].restype = [P, I], None
        fns["margins"].argtypes, fns["margins"].restype = [P, ctypes.POINTER(_QMargins)], None
        _lib = fns
    return _lib


def available() -> bool:
    """Wayland session (and a Qt platform that is actually wayland) + layer-shell-qt present."""
    from PySide6.QtGui import QGuiApplication
    return is_wayland() and QGuiApplication.platformName().startswith("wayland") and bool(_load())


class LayerWindow:
    """Layer-shell handle for one QWidget. Create before the widget is first shown.

    Defaults give a full-screen overlay drawn over panels (the radial menu). Toasts pass
    anchors=bottom|right and zone=0, so they sit in the corner above the panel."""

    def __init__(self, widget, keyboard: int = KBD_NONE, anchors: int = ANCHOR_ALL, zone: int = -1):
        import shiboken6
        from PySide6.QtCore import Qt
        fns = _load()
        widget.setAttribute(Qt.WA_NativeWindow)      # creates the QWindow without showing it
        qwin = shiboken6.getCppPointer(widget.windowHandle())[0]
        self._fns = fns
        self._w = fns["get"](qwin)
        fns["layer"](self._w, LAYER_OVERLAY)
        fns["anchors"](self._w, anchors)
        fns["zone"](self._w, zone)                   # -1: over panels, 0: respect them
        fns["keyboard"](self._w, keyboard)

    def set_keyboard(self, mode: int):
        self._fns["keyboard"](self._w, mode)

    def set_margins(self, left: int = 0, top: int = 0, right: int = 0, bottom: int = 0):
        m = _QMargins(left, top, right, bottom)
        self._fns["margins"](self._w, ctypes.byref(m))
