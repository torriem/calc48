#!/usr/bin/env python3
"""
extract_fonts.py -- dump the HP 48 GX system fonts via the emulator.

This drives libcalc48 to render every character through the calculator's own
`→GROB` command and captures the resulting glyph bitmaps -- so you get the exact
fonts for the loaded ROM with no ROM-address archaeology and no screen-scraping.
Three font sizes are captured:

    →GROB size 1 -> "tiny"      (the minifont, proportional)
    →GROB size 2 -> "standard"  (the 6x8 stack font)
    →GROB size 3 -> "large"

How it works (see docs/stack_io_plan.md for the stack API it builds on):
  * A compiled call to `→GROB` is ROM-version specific, so the tool is
    *self-calibrating*: put the program  « →GROB »  on stack level 1 first; the
    tool reads it back and lifts out the exact compiled word-sequence for
    `→GROB` on your ROM.
    (Aside: `→GROB` *can* also be typed by key injection -- `→` is alpha +
    right-shift + `0`; see docs/keymap.md -- which would avoid the calibration
    step entirely.  This tool uses the push-program + EVAL route instead.)
  * For each character it then builds, in memory, the program
        « "<char>" <size> →GROB »
    (string + real literals we encode ourselves, followed by that word
    sequence), pushes it with hp48_stack_push_object(), presses EVAL, and reads
    the grob back off the stack with hp48_read_object().

GX only.  Non-destructive: it loads the state but never saves it.

Usage:
    # First, on the calc / in x48: put  « →GROB »  on level 1 and save to the
    # state dir.  Then:
    python3 tools/extract_fonts.py [STATE_DIR] [OUT.json]

    STATE_DIR defaults to ~/.hp48 ; OUT.json defaults to tools/hp48_fonts_gx.json
    Set HP48_LIB to point at libcalc48.so if it isn't found automatically.
"""

import importlib.util
import json
import os
import sys

EVAL_KEY = 0x63
DROP_KEY = 0x40
GROB_PROLOG = 0x02B1E
DOCOL = 0x02D9D
SEMI = 0x0312B
SIZES = {1: "tiny", 2: "standard", 3: "large"}
CODE_LO, CODE_HI = 0x20, 0x100        # printable + extended HP charset


def _load_hp48():
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "..", "libcalc48", "hp48.py")
    spec = importlib.util.spec_from_file_location("hp48mod", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.Hp48


# --- nibble helpers (one nibble per list element, LSB-first words) -----------
def n5(v):
    return [v & 0xf, (v >> 4) & 0xf, (v >> 8) & 0xf, (v >> 12) & 0xf, (v >> 16) & 0xf]


def val5(nl):
    return sum(nl[i] << (4 * i) for i in range(5))


def enc_str_char(code):
    """Inline DOCSTR object holding the single byte `code`."""
    length = 2 * 1 + 5                       # length field counts itself (5) + data
    return [0xC, 2, 0xA, 2, 0] + n5(length) + [code & 0xf, (code >> 4) & 0xf]


def enc_real_small(v):
    """Inline DOREAL object for a small non-negative integer (1, 2, 3)."""
    o = [3, 3, 9, 2, 0] + [0] * 16          # prolog + zeroed body (== Real 0)
    o[19] = v                               # leading mantissa digit (exp stays 0)
    return o


def main(argv):
    state_dir = os.path.expanduser(argv[1]) if len(argv) > 1 else os.path.expanduser("~/.hp48")
    here = os.path.dirname(os.path.abspath(__file__))
    out_path = argv[2] if len(argv) > 2 else os.path.join(here, "hp48_fonts_gx.json")

    Hp48 = _load_hp48()
    emu = Hp48()
    if emu.load_state(state_dir) != 0:
        sys.exit("could not load state from %s" % state_dir)
    if not emu._have_stack:
        sys.exit("libcalc48 was built without HP48_WITH_STACK_IO")
    emu.start()
    emu.display_init()
    for _ in range(20):
        emu.run_slice(20000)

    if emu._lib.hp48_object_prolog(emu._lib.hp48_stack_addr(1)) != 0:
        pass
    key = emu.read_object(1)
    if not key or val5(list(key[0:5])) != DOCOL or val5(list(key[-5:])) != SEMI:
        sys.exit("level 1 must hold the program  « →GROB »  (compile it on the "
                 "calc and save the state first)")
    gcall = list(key[5:-5])                 # the →GROB compiled word-sequence
    words = [val5(gcall[i:i + 5]) for i in range(0, len(gcall), 5)]
    print("→GROB call words: " + " ".join("%05x" % w for w in words))
    baseline = emu._lib.hp48_stack_depth()

    def restore():
        for _ in range(12):
            if emu._lib.hp48_stack_depth() <= baseline:
                return
            emu.press(DROP_KEY); emu.run_slice(20000)
            emu.release(DROP_KEY); emu.run_slice(20000)

    fonts = {name: {} for name in SIZES.values()}
    for size, name in SIZES.items():
        captured = 0
        for code in range(CODE_LO, CODE_HI):
            prog = (n5(DOCOL) + enc_str_char(code) + enc_real_small(size)
                    + gcall + n5(SEMI))
            if not emu.push_object(prog):
                restore(); continue
            emu.press(EVAL_KEY)
            for _ in range(6):
                emu.run_slice(20000)
            emu.release(EVAL_KEY)
            for _ in range(6):
                emu.run_slice(20000)

            addr = emu._lib.hp48_stack_addr(1)
            if emu._lib.hp48_object_prolog(addr) == GROB_PROLOG:
                g = emu.read_object(1)
                h = val5(list(g[10:15]))
                w = val5(list(g[15:20]))
                data = bytes(g[20:])         # one nibble per byte, row-major
                fonts[name][code] = {
                    "w": w, "h": h,
                    "data": "".join("%x" % b for b in data),
                }
                captured += 1
            restore()
        print("  %-9s captured %d glyphs" % (name, captured))

    out = {
        "source_rom_dir": state_dir,
        "model": "GX",
        "grob_call_words": ["%05x" % w for w in words],
        "note": "data = grob pixel nibbles (1 nibble/byte, LSB=left); "
                "row stride nibbles = 2*ceil(w/8); pad rows to a byte.",
        "fonts": fonts,
    }
    with open(out_path, "w") as f:
        json.dump(out, f, indent=1)
    total = sum(len(v) for v in fonts.values())
    print("wrote %d glyphs across %d fonts -> %s" % (total, len(fonts), out_path))

    # sanity preview: 'A' in each font
    for name in SIZES.values():
        g = fonts[name].get(ord("A"))
        if not g:
            continue
        w, h = g["w"], g["h"]
        data = [int(c, 16) for c in g["data"]]
        stride = 2 * ((w + 7) // 8)
        print("\n%s 'A' (%dx%d):" % (name, w, h))
        for y in range(h):
            print("  " + "".join("#" if (data[y * stride + (x // 4)] >> (x % 4)) & 1
                                  else "." for x in range(w)))
    emu.destroy()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
