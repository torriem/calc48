#!/usr/bin/env python3
"""
Skeleton: load libcalc48 and bring up an HP 48 emulator instance.

It uses the bundled stdio storage provider rooted at ~/.hp48 -- the same files
the SDL app uses (rom, hp48, ram, port1, port2) -- so the emulator boots from a
saved calculator if one is there.  From here you'd add your own UI / loop.

    python3 examples/calc48_skeleton.py
"""

import ctypes
import os
import sys

# hp48_run_slice() return codes (see hp48.h)
HP48_RUNNING, HP48_HALTED, HP48_DEBUG = 0, 1, 2


def load_libcalc48():
    """Locate and dlopen libcalc48.so."""
    here = os.path.dirname(os.path.abspath(__file__))
    for path in (os.environ.get("CALC48_LIB"),
                 os.path.join(here, "..", "build", "libcalc48.so"),
                 "libcalc48.so"):
        if not path:
            continue
        try:
            return ctypes.CDLL(path)
        except OSError:
            continue
    raise OSError("libcalc48.so not found; build it or set CALC48_LIB")


class Hp48:
    """One emulator instance backed by libcalc48."""

    def __init__(self, home="~/.hp48"):
        self.lib = load_libcalc48()
        self._declare()

        # The stdio provider keeps a pointer to this directory string (via
        # hp48_io_t.user), so it must outlive the emulator: hold it on the
        # instance as a persistent ctypes buffer.
        self._home = ctypes.create_string_buffer(
            os.path.expanduser(home).encode())

        self.h = self.lib.hp48_create()        # allocate + make active
        if not self.h:
            raise MemoryError("hp48_create failed")

    def _declare(self):
        L = self.lib
        L.hp48_create.restype = ctypes.c_void_p
        L.hp48_destroy.argtypes = [ctypes.c_void_p]
        # hp48_load_state() installs the bundled stdio provider rooted at the
        # given directory, then loads rom/hp48/ram/port* from it.
        L.hp48_load_state.argtypes = [ctypes.c_char_p]
        L.hp48_load_state.restype = ctypes.c_int
        L.hp48_init_from_rom.argtypes = [ctypes.c_char_p]
        L.hp48_init_from_rom.restype = ctypes.c_int
        L.hp48_save_state.argtypes = [ctypes.c_char_p]
        L.hp48_save_state.restype = ctypes.c_int
        L.hp48_start.argtypes = []
        L.hp48_display_init.argtypes = []
        L.hp48_run_slice.argtypes = [ctypes.c_int]
        L.hp48_run_slice.restype = ctypes.c_int
        L.hp48_press_key.argtypes = [ctypes.c_int]
        L.hp48_release_key.argtypes = [ctypes.c_int]
        L.hp48_get_lcd.argtypes = [ctypes.POINTER(ctypes.c_int),
                                   ctypes.POINTER(ctypes.c_int)]
        L.hp48_get_lcd.restype = ctypes.POINTER(ctypes.c_ubyte)

    # -- bring-up ---------------------------------------------------------
    def boot(self):
        """Load the saved calculator from ~/.hp48 (stdio provider) and start.

        Returns True if a saved state was loaded, False if there was nothing
        to load (you then need to init_from_rom() with a ROM path)."""
        loaded = self.lib.hp48_load_state(self._home) == 0
        if loaded:
            self.lib.hp48_start()         # one-time run setup
            self.lib.hp48_display_init()  # activate + prime the LCD buffer
        return loaded

    def init_from_rom(self, rom_path):
        """Fresh boot from a ROM image (used when there is no saved state)."""
        if self.lib.hp48_init_from_rom(os.fsencode(rom_path)) != 0:
            return False
        self.lib.hp48_start()
        self.lib.hp48_display_init()
        return True

    # -- run / input / output --------------------------------------------
    def run_slice(self, budget_us=20000):
        """Run ~one frame; the host owns the loop. Returns a HP48_* status."""
        return self.lib.hp48_run_slice(budget_us)

    def press(self, code):       # HP48 key-matrix code (0x8000 = ON)
        self.lib.hp48_press_key(code)

    def release(self, code):
        self.lib.hp48_release_key(code)

    def lcd(self):
        """Return (nibble_bytes, rows, stride) of the live LCD buffer."""
        rows, stride = ctypes.c_int(), ctypes.c_int()
        p = self.lib.hp48_get_lcd(ctypes.byref(rows), ctypes.byref(stride))
        n = rows.value * stride.value
        return bytes(p[i] for i in range(n)), rows.value, stride.value

    # -- shutdown ---------------------------------------------------------
    def save(self):
        return self.lib.hp48_save_state(self._home) == 0

    def close(self):
        if self.h:
            self.lib.hp48_destroy(self.h)
            self.h = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def main():
    with Hp48("~/.hp48") as emu:
        if emu.boot():
            print("Loaded saved calculator from ~/.hp48")
        else:
            print("No saved state in ~/.hp48 -- supply a ROM, e.g.:")
            print("  emu.init_from_rom('~/.hp48/rom')")
            return 0

        # Tiny cooperative loop: run ~0.5s, the way a Qt QTimer or pygame loop
        # would. Add your own event handling / rendering here.
        for _ in range(25):
            emu.run_slice(20000)

        _, rows, stride = emu.lcd()
        print("Running. LCD buffer is %dx%d nibbles." % (rows, stride))
        # emu.save()   # persist back to ~/.hp48 when you're done
    return 0


if __name__ == "__main__":
    sys.exit(main())
