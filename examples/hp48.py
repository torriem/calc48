#!/usr/bin/env python3
"""
ctypes binding for libcalc48 -- the SDL-free HP48 emulator core.

This is a thin, dependency-free wrapper demonstrating that the emulator can be
driven from Python with no UI toolkit.  Run it directly:

    python3 examples/hp48.py                 # binding smoke test (no ROM)
    python3 examples/hp48.py ~/.hp48          # load a saved calc and run it

The second form loads a saved state directory (the same rom/hp48/ram/port*
files the SDL app uses), runs the emulator cooperatively for a moment, and
prints the LCD as ASCII -- the host owns the loop, exactly like a Qt QTimer
would.
"""

import ctypes
import os
import sys

# hp48_run_slice() status codes (see hp48.h)
HP48_RUNNING = 0
HP48_HALTED = 1
HP48_DEBUG = 2


def _find_lib():
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.environ.get("HP48_LIB"),
        os.path.join(here, "..", "build", "libcalc48.so"),
        os.path.join(here, "libcalc48.so"),
        "libcalc48.so",
    ]
    for c in candidates:
        if c and (os.path.exists(c) or c == "libcalc48.so"):
            try:
                return ctypes.CDLL(c)
            except OSError:
                continue
    raise OSError("libcalc48.so not found; build it or set HP48_LIB")


class Hp48:
    """A single emulator instance."""

    def __init__(self, lib=None):
        self._lib = lib or _find_lib()
        self._bind()
        self._h = self._lib.hp48_create()
        if not self._h:
            raise MemoryError("hp48_create failed")

    def _bind(self):
        L = self._lib
        L.hp48_create.restype = ctypes.c_void_p
        L.hp48_destroy.argtypes = [ctypes.c_void_p]
        L.hp48_set_active.argtypes = [ctypes.c_void_p]
        L.hp48_start.argtypes = []
        L.hp48_run_slice.argtypes = [ctypes.c_int]
        L.hp48_run_slice.restype = ctypes.c_int
        L.hp48_step.restype = ctypes.c_int
        L.hp48_press_key.argtypes = [ctypes.c_int]
        L.hp48_release_key.argtypes = [ctypes.c_int]
        L.hp48_load_rom.argtypes = [ctypes.c_char_p]
        L.hp48_load_rom.restype = ctypes.c_int
        L.hp48_init_from_rom.argtypes = [ctypes.c_char_p]
        L.hp48_init_from_rom.restype = ctypes.c_int
        L.hp48_load_state.argtypes = [ctypes.c_char_p]
        L.hp48_load_state.restype = ctypes.c_int
        L.hp48_save_state.argtypes = [ctypes.c_char_p]
        L.hp48_save_state.restype = ctypes.c_int
        L.hp48_display_init.argtypes = []
        L.hp48_get_lcd.argtypes = [ctypes.POINTER(ctypes.c_int),
                                   ctypes.POINTER(ctypes.c_int)]
        L.hp48_get_lcd.restype = ctypes.POINTER(ctypes.c_ubyte)

    # --- lifecycle -------------------------------------------------------
    def activate(self):
        self._lib.hp48_set_active(self._h)

    def destroy(self):
        if self._h:
            self._lib.hp48_destroy(self._h)
            self._h = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.destroy()

    # --- loading / running ----------------------------------------------
    def load_state(self, directory):
        return self._lib.hp48_load_state(os.fsencode(directory))

    def init_from_rom(self, path):
        return self._lib.hp48_init_from_rom(os.fsencode(path))

    def save_state(self, directory):
        return self._lib.hp48_save_state(os.fsencode(directory))

    def start(self):
        self._lib.hp48_start()

    def display_init(self):
        """Activate and prime the LCD for pulling (headless hosts)."""
        self._lib.hp48_display_init()

    def run_slice(self, budget_us=20000):
        return self._lib.hp48_run_slice(budget_us)

    def press(self, code):
        self._lib.hp48_press_key(code)

    def release(self, code):
        self._lib.hp48_release_key(code)

    # --- display ---------------------------------------------------------
    def lcd_nibbles(self):
        """Return (bytearray, rows, stride) of the live LCD nibble buffer."""
        rows = ctypes.c_int()
        stride = ctypes.c_int()
        p = self._lib.hp48_get_lcd(ctypes.byref(rows), ctypes.byref(stride))
        n = rows.value * stride.value
        return bytearray(p[i] for i in range(n)), rows.value, stride.value

    def lcd_ascii(self, width_px=131):
        """Render the LCD as ASCII art (each nibble is 4 horizontal pixels)."""
        buf, rows, stride = self.lcd_nibbles()
        lines = []
        for r in range(rows):
            chars = []
            for x in range(width_px):
                nib = buf[r * stride + (x >> 2)]
                chars.append("#" if (nib >> (x & 3)) & 1 else " ")
            lines.append("".join(chars).rstrip())
        return "\n".join(lines)


def main(argv):
    emu = Hp48()
    _, rows, stride = emu.lcd_nibbles()
    print("created instance; LCD buffer %dx%d nibbles" % (rows, stride))

    # Basic input check: '1' key (matrix code 0x13; see docs/keymap.md).
    emu.press(0x13)
    emu.release(0x13)
    print("pressed/released '1' ok")

    if len(argv) > 1:
        directory = argv[1]
        rc = emu.load_state(directory)
        if rc != 0:
            print("load_state(%r) failed (rc=%d); skipping run" % (directory, rc))
        else:
            print("loaded state from %s; running ~1s ..." % directory)
            emu.start()
            emu.display_init()               # activate + prime the LCD buffer
            for _ in range(50):              # 50 x 20ms = ~1s, host owns loop
                if emu.run_slice(20000) == HP48_DEBUG:
                    break
            print("LCD:")
            print(emu.lcd_ascii())
    else:
        print("(pass a saved-state directory to load a ROM and run, "
              "e.g. ~/.hp48)")

    emu.destroy()
    print("destroyed; done")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
