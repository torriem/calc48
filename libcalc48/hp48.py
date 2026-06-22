#!/usr/bin/env python3
"""
ctypes binding for libcalc48 -- the SDL-free HP48 emulator core.

This is a thin, dependency-free wrapper demonstrating that the emulator can be
driven from Python with no UI toolkit.  Run it directly:

    python3 examples/hp48.py                 # binding smoke test (no ROM)
    python3 examples/hp48.py ~/.hp48          # load a saved calc and run it

The second form loads a saved state directory (the same rom/hp48/ram/port*
files the SDL app uses), runs the emulator cooperatively for a moment, and
prints the LCD using Unicode braille (2x4 dots per character) -- the host owns
the loop, exactly like a Qt QTimer would.  (lcd_ascii() is also available for
plain '#' output.)
"""

import ctypes
import os
import sys

# hp48_run_slice() status codes (see hp48.h)
HP48_RUNNING = 0
HP48_HALTED = 1
HP48_DEBUG = 2


def _find_lib():
    here = os.path.dirname(os.path.realpath(__file__))   # follow any symlink
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

        # RPL user-stack read/write API (hp48_rpl.h) -- present when libcalc48 is
        # built with HP48_WITH_STACK_IO (on by default).  Bind if available.
        self._have_stack = hasattr(L, "hp48_stack_depth")
        if self._have_stack:
            L.hp48_stack_depth.restype = ctypes.c_int
            L.hp48_stack_addr.argtypes = [ctypes.c_int]
            L.hp48_stack_addr.restype = ctypes.c_uint
            L.hp48_object_size.argtypes = [ctypes.c_uint]
            L.hp48_object_size.restype = ctypes.c_int
            L.hp48_object_prolog.argtypes = [ctypes.c_uint]
            L.hp48_object_prolog.restype = ctypes.c_uint
            L.hp48_stack_describe.argtypes = [ctypes.c_int, ctypes.c_char_p,
                                              ctypes.c_int]
            L.hp48_stack_describe.restype = ctypes.c_int
            L.hp48_read_object.argtypes = [ctypes.c_uint,
                                           ctypes.POINTER(ctypes.c_ubyte),
                                           ctypes.c_int]
            L.hp48_read_object.restype = ctypes.c_int
            L.hp48_stack_push_object.argtypes = [ctypes.POINTER(ctypes.c_ubyte),
                                                 ctypes.c_int]
            L.hp48_stack_push_object.restype = ctypes.c_int
            L.hp48_push_real.argtypes = [ctypes.c_double]
            L.hp48_push_real.restype = ctypes.c_int
            L.hp48_push_string.argtypes = [ctypes.c_char_p, ctypes.c_int]
            L.hp48_push_string.restype = ctypes.c_int

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

    # --- RPL stack (HP48_WITH_STACK_IO build) ---------------------------
    def stack(self):
        """Return a list of (level, prolog, size, text) for the RPL stack,
        top (level 1) first.  Empty if the build lacks the GX stack API."""
        if not self._have_stack:
            return []
        out = []
        buf = ctypes.create_string_buffer(512)
        for lvl in range(1, self._lib.hp48_stack_depth() + 1):
            addr = self._lib.hp48_stack_addr(lvl)
            prolog = self._lib.hp48_object_prolog(addr)
            size = self._lib.hp48_object_size(addr)
            n = self._lib.hp48_stack_describe(lvl, buf, len(buf))
            text = buf.value.decode("latin-1") if n >= 0 else "?"
            out.append((lvl, prolog, size, text))
        return out

    def read_object(self, level):
        """Return the raw nibbles (bytes, one nibble each) of the object at
        `level`, or None if unavailable."""
        if not self._have_stack:
            return None
        addr = self._lib.hp48_stack_addr(level)
        size = self._lib.hp48_object_size(addr)
        if size <= 0:
            return None
        buf = (ctypes.c_ubyte * size)()
        n = self._lib.hp48_read_object(addr, buf, size)
        return bytes(buf[:n]) if n > 0 else None

    def push_object(self, nibbles):
        """Push a complete RPL object (raw nibbles, prologue first) onto level 1.
        Returns True on success.  Call only when the calc is idle."""
        if not self._have_stack:
            return False
        arr = (ctypes.c_ubyte * len(nibbles))(*nibbles)
        return self._lib.hp48_stack_push_object(arr, len(nibbles)) == 0

    def push_real(self, value):
        """Push a number as an HP 48 Real.  Returns True on success."""
        return self._have_stack and self._lib.hp48_push_real(float(value)) == 0

    def push_string(self, text):
        """Push a string (HP 48 character string).  Returns True on success."""
        if not self._have_stack:
            return False
        b = text.encode("latin-1") if isinstance(text, str) else bytes(text)
        return self._lib.hp48_push_string(b, len(b)) == 0

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

    def lcd_braille(self, width_px=131):
        """Render the LCD with Unicode braille.

        Each braille glyph (U+2800..U+28FF) packs a 2x4 dot grid, so it shows 8
        pixels per character -- the densest text rendering available.  The whole
        131x64 LCD collapses to ~66x16 characters at roughly true aspect ratio.
        Needs a UTF-8 terminal and a font with braille glyphs (most have them).
        """
        buf, rows, stride = self.lcd_nibbles()

        def on(x, y):
            if not (0 <= x < width_px and 0 <= y < rows):
                return 0
            return (buf[y * stride + (x >> 2)] >> (x & 3)) & 1

        # Braille dot bit for (row dy 0..3, col dx 0..1), added to U+2800.
        dot = ((0x01, 0x08), (0x02, 0x10), (0x04, 0x20), (0x40, 0x80))
        lines = []
        for y0 in range(0, rows, 4):
            cells = []
            for x0 in range(0, width_px, 2):
                bits = 0
                for dy in range(4):
                    for dx in range(2):
                        if on(x0 + dx, y0 + dy):
                            bits |= dot[dy][dx]
                cells.append(chr(0x2800 + bits))
            lines.append("".join(cells).rstrip("\u2800"))  # trim blank (U+2800) cells
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
            print("LCD (braille, 2x4 dots/char):")
            print(emu.lcd_braille())

            stk = emu.stack()
            if stk:
                print("\nRPL stack (level 1 = top):")
                for lvl, prolog, size, text in stk:
                    print("  %d: prolog=%05x size=%-4d %s"
                          % (lvl, prolog, size, text))

                # Demonstrate writing (in-memory only; not saved): push a
                # number and a string with the typed conveniences.
                emu.push_real(2.718281828)
                emu.push_string("hi from python")
                print("\npushed a Real and a String; stack is now:")
                for lvl, prolog, size, text in emu.stack():
                    print("  %d: %s" % (lvl, text))
            elif not emu._have_stack:
                print("\n(stack read/write API not in this build; "
                      "configure -DHP48_WITH_STACK_IO=ON)")
    else:
        print("(pass a saved-state directory to load a ROM and run, "
              "e.g. ~/.hp48)")

    emu.destroy()
    print("destroyed; done")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
