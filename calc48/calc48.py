#!/usr/bin/env python3
r"""
calc48.py -- a command-line HP 48 RPL calculator (built on libcalc48).

Input modes, chosen automatically.  Piped/redirected stdin always wins: if it
is present, stdin is the program and every positional argument is RPL placed on
the stack.  Otherwise (stdin is a terminal) the first argument, if any, is a
file to run.

  * Piped/redirected stdin: each line of stdin is evaluated, the resulting stack
    is printed, and all positional args are pushed first.
        echo "2 3 + 4 *" | python3 calc48/calc48.py        # -> 1: 20
        echo "+" | python3 calc48/calc48.py 3 4            # 3,4 then stdin -> 7
        python3 calc48/calc48.py < script.rpl              # run a file: redirect
  * Terminal + a file argument: run RPL from that file (one statement per line),
    printing the resulting stack; further args are pushed onto the stack first.
        python3 calc48/calc48.py script.rpl
        python3 calc48/calc48.py script.rpl 3 4        # stack 3,4 then run it
  * Terminal, no file argument: an interactive REPL with a "calc48>" prompt and
    (if the readline module is available) line editing, history, and tab
    completion.  Lines are RPL; a line starting with '.' is a meta-command --
    `.help`, `.stack`, `.clear`, `.lcd [ascii]` (show the screen), `.key KEY...`
    (tap keys by name or matrix code), `.keys` (list key names), `.chars` (list
    special-character spellings), `.store FILE` / `.load FILE` (save/load a stack
    object in HP binary format), `.save [DIR]`, `.quit`.
        python3 calc48/calc48.py

The first error aborts file/stdin runs with a message on stderr and a non-zero
exit code.

It needs the HP 48 ROM -- from --rom FILE, else $HP48_ROM, else
<config_dir>/hp48/rom -- and the embedded blank state (build it once with
tools/make_blank_state.py).  If the ROM is missing it prints where to download
and place it.

By default every run is a pristine, independent calculator and nothing is saved.
To work in a persistent *profile* (its own hp48 + ram, separate from the ROM):

    -s/--state DIR   load the state (hp48 + ram) from DIR (or start blank if DIR
                     has none yet); DIR is also the save target
    -w/--save        automatically write the state back to DIR on a clean exit
                     (requires --state)

So `--state DIR` alone runs a profile without auto-saving (read-only unless you
explicitly `.save` in the REPL); `--state DIR --save` also persists on exit.
The auto-save is skipped if a file/stdin run aborts on an error.  In the REPL,
`.save [DIR]` writes the current state on demand (to DIR, or the --state dir).

Input conveniences (translated to HP 48 characters before compiling; both skip
the inside of "..." string literals):
  * ASCII digraphs: `<<`->`«`, `>>`->`»`, `->`->`→`, `<=`->`≤`, `>=`->`≥`,
    `!=`->`≠`  (Rpl.DIGRAPHS).
  * HP 48 backslash escapes (what ->STR / ASCII transfer use): `\pi`->π,
    `\GS`->Σ, `\Gd`->δ, `\oo`->∞, ...  (Rpl.NAMED).  E.g. `2 \pi * ->NUM`.

The RPL engine itself (the Rpl class) is the same one documented in rpl_eval.py.
Requires libcalc48 built with HP48_WITH_STACK_IO (the default)."""

import argparse
import base64
import ctypes
import gzip
import os
import sys
from collections import namedtuple

# The Python binding (hp48.py) and the generated blank-state module live with
# the library; depend only on libcalc48, never on examples/.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "libcalc48"))
from hp48 import Hp48          # noqa: E402
# _blank_state (the embedded blank GX RAM/CPU image) is imported lazily in
# __init__ -- it is ROM-derived and generated locally by tools/make_blank_state.py
# rather than committed, so calc48 must import cleanly without it.

ROM_URL = "https://www.hpcalc.org/details/4524"


def config_dir(app="hp48"):
    """Cross-platform per-user config dir (stdlib; XDG on Unix)."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, app)


def find_rom():
    """The HP 48 ROM path: $HP48_ROM, else <config_dir>/rom.  None if missing."""
    for cand in (os.environ.get("HP48_ROM"), os.path.join(config_dir(), "rom")):
        if cand and os.path.isfile(cand):
            return cand
    return None


class RomNotFoundError(Exception):
    """No ROM found; .message tells the user where to get and put one."""

    def __init__(self):
        rom = os.path.join(config_dir(), "rom")
        self.message = (
            "HP 48 ROM not found.\n"
            "Download the HP 48GX ROM from:\n    %s\n"
            "and save the ROM image as:\n    %s\n"
            "(or set HP48_ROM=/path/to/rom)" % (ROM_URL, rom))
        super().__init__(self.message)


# ---- in-memory storage provider (hp48_io_t) -------------------------------
# The core pulls each resource (rom/hp48/ram/...) through these callbacks and
# copies it in; we serve the ROM file + the embedded blank RAM/CPU image, and
# never save.  The load callback must return a buffer the core frees with C
# free(), so allocate it with the C runtime's malloc.
_LOADCB = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_char_p,
                           ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte)),
                           ctypes.POINTER(ctypes.c_size_t))
_SAVECB = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_char_p,
                           ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t)


class _Hp48Io(ctypes.Structure):
    _fields_ = [("user", ctypes.c_void_p), ("load", _LOADCB), ("save", _SAVECB)]


_libc = ctypes.CDLL(None)       # process libc (glibc / macOS libSystem)
_libc.malloc.restype = ctypes.c_void_p
_libc.malloc.argtypes = [ctypes.c_size_t]

# What eval() returns: the FULL stack (decoded, top first -> stack[0] is the
# top/result) and the error (an RplError or None).  So a caller can always do
# res.stack[0] for "the result", inspect the rest, and check res.error.
Result = namedtuple("Result", "stack error")

# Object prologs (see libcalc48/include/rpl.h)
DOREAL, DOEREL, DOCSTR = 0x02933, 0x02955, 0x02a2c
DOHSTR, DOSYMB = 0x02a4e, 0x02ab8       # binary integer, algebraic/symbolic

# Key matrix codes (see docs/keymap.md).  Letters = ALPHA then the key.
ALPHA, SHR, ENTER, DROP, ON = 0x35, 0x15, 0x44, 0x40, 0x8000
# "STR→" + ENTER :  S(alpha+SIN) T(alpha+COS) R(alpha+RIGHT) →(alpha+SHR+0)
STR_TO_KEYS = [ALPHA, 0x34, ALPHA, 0x54, ALPHA, 0x60, ALPHA, SHR, 0x03, ENTER]

# Friendly key names -> matrix code, for the `.key` meta-command (see
# docs/keymap.md).  Digits and a-z follow the HP 48 alpha / PC-key layout, so
# e.g. `s` is the SIN key and `g` is MTH; combine with `alpha` to type letters.
KEY_NAMES = {}
for _c, _code in zip("0123456789",
                     (0x03, 0x13, 0x12, 0x11, 0x23, 0x22, 0x21, 0x33, 0x32, 0x31)):
    KEY_NAMES[_c] = _code
for _c, _code in zip("abcdefghijklmnopqrstuvwxyz",
                     (0x14, 0x84, 0x83, 0x82, 0x81, 0x80, 0x24, 0x74, 0x73, 0x72,
                      0x71, 0x70, 0x04, 0x64, 0x63, 0x62, 0x61, 0x60, 0x34, 0x54,
                      0x53, 0x52, 0x51, 0x50, 0x43, 0x42)):
    KEY_NAMES[_c] = _code
KEY_NAMES.update({
    "enter": 0x44, "on": ON, "alpha": ALPHA,
    "lshift": 0x25, "shl": 0x25, "rshift": SHR, "shr": SHR,
    "del": 0x41, "delete": 0x41,
    "back": 0x40, "bs": 0x40, "bksp": 0x40, "backspace": 0x40,
    "up": 0x71, "down": 0x61, "left": 0x62, "right": 0x60,
    "space": 0x01, "spc": 0x01,
    "+": 0x00, "plus": 0x00, "-": 0x10, "minus": 0x10,
    "*": 0x20, "mul": 0x20, "/": 0x30, "div": 0x30,
    ".": 0x02, "dot": 0x02, "period": 0x02, "'": 0x04, "tick": 0x04,
    "neg": 0x43, "chs": 0x43, "eex": 0x42,
    "sto": 0x64, "eval": 0x63, "sin": 0x34, "cos": 0x54, "tan": 0x53,
    "sqrt": 0x52, "inv": 0x50, "pow": 0x51, "^": 0x51,
    "mth": 0x24, "prg": 0x74, "cst": 0x73, "var": 0x72,
    "nxt": 0x70, "next": 0x70,
})
del _c, _code


def _parse_key(tok):
    """A key token -> matrix code.  A known name (case-insensitive) wins;
    otherwise the token is a hex matrix code (0x optional).  Raises ValueError."""
    t = tok.lower()
    if t in KEY_NAMES:
        return KEY_NAMES[t]
    return int(t[2:] if t.startswith("0x") else t, 16)


# HP 48 code -> Unicode glyph, for the special block 0x80-0x9f (the rest of the
# high range follows Latin-1, so chr(code) is the glyph there).  Used by the
# `.chars` listing.
_HP_GLYPH = {
    0x80: "∠", 0x81: "x̄", 0x82: "∇", 0x83: "√",
    0x84: "∫", 0x85: "Σ", 0x87: "π", 0x88: "∂",
    0x89: "≤", 0x8a: "≥", 0x8b: "≠", 0x8c: "α",
    0x8d: "→", 0x8e: "←", 0x91: "γ", 0x92: "δ",
    0x93: "ε", 0x94: "η", 0x95: "θ", 0x96: "λ",
    0x97: "ρ", 0x98: "σ", 0x99: "τ", 0x9a: "ω",
    0x9b: "Δ", 0x9c: "Π", 0x9d: "Ω", 0x9f: "∞",
}


def _hp_glyph(code):
    return _HP_GLYPH.get(code) or bytes([code & 0xff]).decode("latin-1", "replace")


# Inverse of _HP_GLYPH: a typed Unicode glyph -> the HP 48 byte (as a Latin-1
# char, which push_string encodes directly).  Only the 0x80-0x9f block needs
# this; HP chars >= 0xa0 are Latin-1, so a typed « / µ / × already encodes as-is.
_GLYPH_TO_HP = {glyph: chr(code) for code, glyph in _HP_GLYPH.items()}

# HP 48 binary object transfer format: an 8-byte ASCII header then the object
# nibbles packed two-per-byte (low nibble first).  Compatible with droid48,
# x48, Emu48, Conn, and a real HP 48 (which ignore the revision letter on read;
# 'R' is the G/GX family).  See droid48 binio.c.
HP48_BIN_HEADER = b"HPHP48-R"


def _pack_nibbles(nibs):
    """nibbles (one per byte) -> bytes packed two-per-byte, low nibble first."""
    out = bytearray((len(nibs) + 1) // 2)
    for i, nib in enumerate(nibs):
        if i & 1:
            out[i >> 1] |= (nib & 0xf) << 4
        else:
            out[i >> 1] = nib & 0xf
    return bytes(out)


def _unpack_nibbles(data):
    """bytes -> nibbles (one per byte), low nibble first."""
    nibs = bytearray(2 * len(data))
    for i, b in enumerate(data):
        nibs[2 * i] = b & 0xf
        nibs[2 * i + 1] = b >> 4
    return nibs


# Object sizing (mirror of obj_size() in libcalc48/src/rplstack.c), so .load can
# trim the byte-padded file body to the object's exact nibble length before
# pushing -- an over-long TEMPOB block corrupts the calculator's GC.
_SZ_FIXED = {0x02911: 10, 0x02933: 21, 0x02955: 26, 0x02977: 37, 0x0299d: 47,
             0x029bf: 7, 0x02baa: 15, 0x02e92: 11}      # SysBin..Char, AcPtr, ROMP
_SZ_COMPOSITE = {0x02a74, 0x02ab8, 0x02ada, 0x02d9d}    # List, Symb, Unit, Prog
_SZ_NAME = {0x02e48, 0x02e6d}                            # Global/Local Ident
_SZ_LENPREFIX = {0x029e8, 0x02a0a, 0x02a2c, 0x02a4e, 0x02b1e, 0x02b40, 0x02b62,
                 0x02b88, 0x02bcc, 0x02bee, 0x02c10, 0x02dcc}  # Array..Code
_SZ_TAG, _SZ_RRP, _SZ_SEMI = 0x02afc, 0x02a96, 0x0312b


def _nib_val(nibs, pos, n):
    return sum(nibs[pos + i] << (4 * i) for i in range(n)) if pos + n <= len(nibs) else -1


def _object_nibble_size(nibs, pos=0, depth=0):
    """Total nibble length of the object at `pos`, or -1 if it can't be sized."""
    if depth > 256 or pos + 5 > len(nibs):
        return -1
    pro = _nib_val(nibs, pos, 5)
    if pro in _SZ_FIXED:
        return _SZ_FIXED[pro]
    if pro == _SZ_SEMI:
        return 0
    if pro in _SZ_COMPOSITE:
        length, n = 0, 5
        while True:
            length += n
            e = _object_nibble_size(nibs, pos + length, depth + 1)
            if e < 0:
                return -1
            if e == 0:
                break
            n = e
        return length + 5
    if pro in _SZ_NAME:
        return 7 + _nib_val(nibs, pos + 5, 2) * 2
    if pro == _SZ_TAG:
        n = 7 + _nib_val(nibs, pos + 5, 2) * 2
        rest = _object_nibble_size(nibs, pos + n, depth + 1)
        return -1 if rest < 0 else n + rest
    if pro == _SZ_RRP:
        n = _nib_val(nibs, pos + 8, 5)
        if n == 0:
            return 13
        length = 8 + n
        length += _nib_val(nibs, pos + length, 2) * 2 + 4
        rest = _object_nibble_size(nibs, pos + length, depth + 1)
        return -1 if rest < 0 else length + rest
    if pro in _SZ_LENPREFIX:
        return 5 + _nib_val(nibs, pos + 5, 5)
    return 5


_ESCAPE_GLYPHS = None


def _to_unicode(text):
    """Replace HP 48 backslash escapes (\\pi, \\GS, \\<<, ...) in decoded display
    text with their Unicode glyphs, so the printed stack reads naturally
    (strings, algebraics, programs).  Longest spelling first so \\<< wins over
    \\<."""
    global _ESCAPE_GLYPHS
    if _ESCAPE_GLYPHS is None:
        _ESCAPE_GLYPHS = sorted(
            ((spel, _hp_glyph(code)) for spel, code in Rpl.NAMED.items()),
            key=lambda sg: len(sg[0]), reverse=True)
    for spel, glyph in _ESCAPE_GLYPHS:
        if spel in text:
            text = text.replace(spel, glyph)
    return text


class RplError(Exception):
    """An RPL error: a syntax error, or a trapped runtime error.

    .message -- the calculator's text (e.g. 'Bad Argument Type'); .number --
    the HP error number for runtime errors (e.g. '#203h'), else None."""

    def __init__(self, message, number=None):
        super().__init__(message if not number else "%s (%s)" % (message, number))
        self.message = message
        self.number = number


class Rpl:
    """An HP 48 instance you can script with User RPL source strings."""

    # ASCII digraphs -> HP 48 single-character codes, so you can type plain
    # ASCII instead of the special glyphs.  Applied to source by eval() (outside
    # string literals).  Extend as needed: Rpl.DIGRAPHS["->>"] = 0x...
    DIGRAPHS = {
        "<<": 0xAB,   # «  (program start)
        ">>": 0xBB,   # »  (program end)
        "->": 0x8D,   # →  (→GROB, →LIST, →NUM, local vars, ...)
        "<=": 0x89,   # ≤
        ">=": 0x8A,   # ≥
        "!=": 0x8B,   # ≠
    }

    # HP 48 backslash escapes for special characters (the same ones the calc's
    # ASCII-transfer format and ->STR use, so input matches output).  STR→ does
    # NOT decode these itself, so eval() converts them to the HP byte.  Harvested
    # from the ROM via ->STR; codes are the HP 48 character set.  Type e.g.
    # "\pi" to push π, "2 \pi *", "\GS" for Σ, "\oo" for ∞.
    NAMED = {
        r"\<":  0x80, r"\x-": 0x81, r"\.V": 0x82, r"\v/": 0x83, r"\.S": 0x84,
        r"\GS": 0x85, r"\pi": 0x87, r"\.d": 0x88, r"\<=": 0x89, r"\>=": 0x8a,
        r"\=/": 0x8b, r"\Ga": 0x8c, r"\->": 0x8d, r"\<-": 0x8e, r"\Gg": 0x91,
        r"\Gd": 0x92, r"\Ge": 0x93, r"\Gn": 0x94, r"\Gh": 0x95, r"\Gl": 0x96,
        r"\Gr": 0x97, r"\Gs": 0x98, r"\Gt": 0x99, r"\Gw": 0x9a, r"\GD": 0x9b,
        r"\PI": 0x9c, r"\GW": 0x9d, r"\oo": 0x9f, r"\<<": 0xab, r"\Gm": 0xb5,
        r"\>>": 0xbb, r"\.x": 0xd7, r"\O/": 0xd8,
    }

    def __init__(self, state_dir=None, profile=None, save_dir=None,
                 rom=None, lib=None):
        """Boot a calculator.  The state (RAM + CPU) comes from one of:
          * the embedded blank GX image (default -- a pristine, independent
            calc),
          * `profile`: a directory's `hp48`+`ram` files (the ROM still comes
            from find_rom()), or
          * `state_dir`: a full saved-state directory loaded via the bundled
            stdio provider (rom/hp48/ram/port*; used by make_blank_state).
        If `save_dir` is set, save() persists `hp48`+`ram` back there; the
        caller decides when (e.g. on clean exit).  Nothing is saved otherwise."""
        self.emu = Hp48(lib)
        if not self.emu._have_stack:
            raise RuntimeError("libcalc48 built without HP48_WITH_STACK_IO")
        self.save_dir = os.path.expanduser(save_dir) if save_dir else None
        if state_dir is not None:
            if self.emu.load_state(os.path.expanduser(state_dir)) != 0:
                raise RuntimeError("could not load state from %s" % state_dir)
        else:
            rom_path = rom or find_rom()
            if not rom_path:
                raise RomNotFoundError()
            if profile is not None:
                pdir = os.path.expanduser(profile)
                hp48 = open(os.path.join(pdir, "hp48"), "rb").read()
                ram = open(os.path.join(pdir, "ram"), "rb").read()
            else:
                try:
                    import _blank_state
                except ImportError:
                    raise RuntimeError(
                        "no embedded blank image; generate it with "
                        "tools/make_blank_state.py")
                hp48 = gzip.decompress(base64.b64decode(_blank_state.HP48_GZ_B64))
                ram = gzip.decompress(base64.b64decode(_blank_state.RAM_GZ_B64))
            self._load_blobs({
                "rom": open(rom_path, "rb").read(), "hp48": hp48, "ram": ram})
        self.emu.start()
        self.emu.display_init()
        self._settle(20)
        self.clear()                       # start from an empty stack

    def save(self):
        """Persist the current state (hp48 + ram) to save_dir.  Returns 0 on
        success, <0 on error; raises if no save_dir was configured."""
        if not self.save_dir:
            raise RuntimeError("save() called but no save_dir configured")
        return self.emu.save_state(self.save_dir)

    def _load_blobs(self, blobs):
        """Install an in-memory hp48_io_t that serves `blobs` (name -> bytes)
        and never saves, then have the core read the state from them."""
        def load(user, name, data_pp, len_p):
            blob = blobs.get(name.decode("latin-1"))
            if blob is None:
                return -1                  # absent (ports) / required -> error
            n = len(blob)
            buf = _libc.malloc(n if n else 1)
            if not buf:
                return -1
            if n:
                ctypes.memmove(buf, blob, n)
            data_pp[0] = ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte))
            len_p[0] = n
            return 0

        def save(user, name, data, n):
            return 0                       # rpl_eval never persists

        self._io_load = _LOADCB(load)      # keep these alive for the instance
        self._io_save = _SAVECB(save)
        self._io = _Hp48Io(None, self._io_load, self._io_save)
        L = self.emu._lib
        L.hp48_set_io.argtypes = [ctypes.POINTER(_Hp48Io)]
        L.read_files.restype = ctypes.c_int
        L.hp48_set_io(ctypes.byref(self._io))
        if L.read_files() != 0:
            raise RuntimeError("read_files() failed to load the embedded image")

    # -- low-level driving -------------------------------------------------
    def _settle(self, n=6):
        for _ in range(n):
            self.emu.run_slice(20000)

    def _key(self, code):
        self.emu.press(code); self._settle(2)
        self.emu.release(code); self._settle(2)

    def depth(self):
        return self.emu._lib.hp48_stack_depth()

    def clear(self):
        """Drop everything on the stack."""
        for _ in range(500):
            if self.depth() <= 0:
                break
            self._key(DROP)

    def _drop(self, n):
        for _ in range(n):
            self._key(DROP)

    # -- result decoding ---------------------------------------------------
    def _describe(self, level):
        buf = ctypes.create_string_buffer(4096)
        n = self.emu._lib.hp48_stack_describe(level, buf, len(buf))
        return buf.value.decode("latin-1") if n >= 0 else ""

    def _calc_str(self, level):
        """The calculator's OWN display string for a stack level, via `→STR`.
        Used for binary integers and algebraics so they show in the live base /
        as infix (matching the real calc) rather than our raw hex/RPN decode.
        Returns the text, or None on failure; always restores the stack."""
        d = self.depth()
        # "<level> PICK →STR": copy the level to the top and stringify it.
        if not self.emu.push_string("%d PICK %cSTR" % (level, 0x8d)):
            return None
        for k in STR_TO_KEYS:
            self._key(k)
        self._settle(8)
        text = None
        if (self.depth() == d + 1 and self.emu._lib.hp48_object_prolog(
                self.emu._lib.hp48_stack_addr(1)) == DOCSTR):
            text = self._decode(1)         # the →STR result string's contents
        while self.depth() > d:            # restore the stack
            self._key(DROP)
        return text

    def _decode(self, level):
        """Return a Python value for the object at `level`: Real -> float,
        String -> str, binary integer / algebraic -> the calc's own display
        text (live base / infix, via →STR), everything else -> its RPL text."""
        addr = self.emu._lib.hp48_stack_addr(level)
        prolog = self.emu._lib.hp48_object_prolog(addr)
        if prolog in (DOHSTR, DOSYMB):
            s = self._calc_str(level)
            if s is not None:
                return s
        text = self._describe(level)
        data = text.split("] ", 1)[1] if "] " in text else text
        if prolog in (DOREAL, DOEREL):
            try:
                return float(data)
            except ValueError:
                return data
        if prolog == DOCSTR:
            return data[1:-1] if len(data) >= 2 and data[0] == '"' else data
        return data

    @staticmethod
    def _string_object(src_bytes):
        """The nibbles (one per byte) hp48_push_string produces for src_bytes --
        used to detect when STR→ left the source unconsumed (an error)."""
        n = len(src_bytes)
        lf = 2 * n + 5
        out = [0xC, 2, 0xA, 2, 0,
               lf & 0xf, (lf >> 4) & 0xf, (lf >> 8) & 0xf,
               (lf >> 12) & 0xf, (lf >> 16) & 0xf]
        for c in src_bytes:
            out += [c & 0xf, (c >> 4) & 0xf]
        return bytes(out)

    def _translate(self, src):
        """Map input spellings to HP 48 characters.  First, typed Unicode glyphs
        (π, √, Σ, →, ≤, ...) are converted everywhere -- including inside "..."
        strings, since a typed glyph *is* that character.  Then ASCII digraphs
        (<<, >>, ->, ...) and backslash escapes (\\pi, \\GS, ...) are converted
        outside string literals only (so literal '->' in a string is kept).
        Longest match wins, so >> vs >=, and \\<< vs \\<, don't collide."""
        for glyph in sorted(_GLYPH_TO_HP, key=len, reverse=True):
            if glyph in src:
                src = src.replace(glyph, _GLYPH_TO_HP[glyph])
        subs = dict(self.DIGRAPHS)
        subs.update(self.NAMED)
        keys = sorted(subs, key=len, reverse=True)
        out = []
        i, n, in_str = 0, len(src), False
        while i < n:
            c = src[i]
            if c == '"':
                in_str = not in_str
                out.append(c); i += 1; continue
            if not in_str:
                for k in keys:
                    if src.startswith(k, i):
                        out.append(chr(subs[k])); i += len(k); break
                else:
                    out.append(c); i += 1
                continue
            out.append(c); i += 1
        return "".join(out)

    # -- the scripting entry point ----------------------------------------
    def eval(self, src, translate=True):
        """Compile + run User RPL `src`.  Returns a Result(stack, error):

          .stack -- the FULL stack afterwards, decoded, top first (so
                    res.stack[0] is "the result"; the rest is below it).
          .error -- None on success, else an RplError.  Both a *syntax* error
                    (won't parse) and a *runtime* error (Bad Argument Type,
                    Infinite Result, ...) are reported here, not raised -- the
                    caller checks res.error.  RplError.message is the
                    calculator's text; .number is the HP error number.

        Runtime errors are trapped with RPL's IFERR; the items the failed line
        pushed are then dropped (depth restored to before the line), so .stack
        reflects a clean pre-line stack.  (Items it mutated/consumed before
        erroring are not restored -- see the note below on full rollback.)

        With translate=True (default), ASCII digraphs in DIGRAPHS are converted
        to HP 48 characters first (e.g. << -> «, -> -> →)."""
        if translate:
            src = self._translate(src)
        self._key(ON)                      # clear any prior error / edit state
        self._settle()

        # Wrap in an error trap.  On error: roll back, push  ERRN ERRM 1.
        # On success: push 0.  The flag at level 1 tells us which happened.
        wrapped = "IFERR " + src + " THEN ERRN ERRM 1 ELSE 0 END"
        wrap_bytes = wrapped.encode("latin-1")
        before = self.depth()
        if not self.emu.push_string(wrapped):
            raise RplError("could not push source onto the stack")
        for k in STR_TO_KEYS:
            self._key(k)
        self._settle(12)
        after = self.depth()

        # Syntax error: STR→ left the (whole wrapped) source unparsed on top.
        if after == before + 1 and self.emu.read_object(1) == \
                self._string_object(wrap_bytes):
            self._key(DROP); self._settle()
            return Result(self.stack(), RplError("syntax error: %r" % src))

        flag = self._decode(1)             # 1.0 = trapped error, 0.0 = success
        if flag == 1.0:
            message = self._decode(2)      # ERRM (error message string)
            number = self._describe(3).split("] ", 1)[-1]   # ERRN (e.g. #203h)
            self._drop(3)                  # flag, ERRM, ERRN
            # IFERR catches the error but doesn't unwind the stack, so the
            # failed line may have left partial items -- drop them so the stack
            # is back to its depth from before this line.
            while self.depth() > before:
                self._key(DROP)
            return Result(self.stack(), RplError(message, number))

        self._drop(1)                      # the success flag
        return Result(self.stack(), None)

    def eval1(self, src):
        """Convenience: return just the top result (stack[0]), or None if the
        stack is empty.  Raises the RplError if `src` failed."""
        res = self.eval(src)
        if res.error:
            raise res.error
        return res.stack[0] if res.stack else None

    # -- whole-stack views -------------------------------------------------
    def stack(self):
        """The whole stack as decoded Python values, top first (level 1 = [0])."""
        d = self.depth()
        return [self._decode(lvl) for lvl in range(1, d + 1)]

    def _level_text(self, level):
        """The calculator's representation of a level for printing.  Binary
        integers and algebraics go through →STR (live base / infix); HP 48
        special characters (strings, algebraics, programs) are shown as their
        Unicode glyphs rather than the \\pi-style ASCII spellings."""
        prolog = self.emu._lib.hp48_object_prolog(
            self.emu._lib.hp48_stack_addr(level))
        if prolog in (DOHSTR, DOSYMB):
            s = self._calc_str(level)
            if s is not None:
                return _to_unicode(s)
        t = self._describe(level)
        t = t.split("] ", 1)[1] if "] " in t else t
        return _to_unicode(t)

    def show(self, limit=8):
        """Print the stack like a calculator: top `limit` levels, level 1 at the
        bottom (set limit=None for the whole stack)."""
        d = self.depth()
        if d == 0:
            print("  (empty stack)")
            return
        start = d if (limit is None or d <= limit) else limit
        if start < d:
            print("   ⋮  (%d deeper level%s not shown)"
                  % (d - start, "" if d - start == 1 else "s"))
        for lvl in range(start, 0, -1):
            print("  %d: %s" % (lvl, self._level_text(lvl)))

    def close(self):
        self.emu.destroy()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


_META_CMDS = ["stack", "clear", "lcd", "key", "keys", "chars", "store", "load",
              "save", "help", "quit"]

_META_HELP = """calc48 meta-commands (a leading '.'; everything else is RPL):
  .stack        show the whole stack
  .clear        empty the stack
  .lcd [ascii]  show the calculator screen as braille (or ascii)
  .key KEY...   tap key(s) by name or hex code, then show the screen
                (e.g. .key 1 2 enter); '.keys' lists the names
  .keys         list the .key names
  .chars        list the ASCII spellings for HP 48 special characters
  .store FILE   write the level-1 object to FILE (HP binary object format)
  .load FILE    push an object from FILE (a non-HP48 file loads as a string)
  .save [DIR]   save the whole calculator state to DIR, else the --state dir
  .help [keys]  this list (or the key names)
  .quit         exit  (bare 'quit'/'exit' also work)"""


def _print_chars(rpl):
    """List the input spellings for HP 48 special characters and the glyph each
    produces (the translation applies outside "..." string literals)."""
    print("ASCII spellings for HP 48 special characters:")
    print("  digraphs:  " + "   ".join(
        "%s=%s" % (k, _hp_glyph(rpl.DIGRAPHS[k]))
        for k in ("<<", ">>", "->", "<=", ">=", "!=")))
    print("  backslash escapes:")
    entries = ["%s=%s" % (s, _hp_glyph(c))
               for s, c in sorted(rpl.NAMED.items(), key=lambda kv: kv[1])]
    for i in range(0, len(entries), 6):
        print("    " + "".join("%-10s" % e for e in entries[i:i + 6]))

_KEYS_HELP = """.key names (case-insensitive; see docs/keymap.md):
  digits   0-9
  letters  a-z   (HP 48 alpha layout; prefix with 'alpha' to type a letter)
  editing  enter  del/delete  back/bs/bksp  alpha  on
  shifts   lshift/shl  rshift/shr
  arrows   up  down  left  right
  math     +/plus  -/minus  */mul  //div  neg/chs  eex  ^/pow  sqrt  inv
           sin  cos  tan
  menus    mth  prg  cst  var  nxt/next  sto  eval
  misc     space/spc  ./dot  '/tick
  ...or any hex matrix code (0x optional), e.g. .key 8000 (ON) 44 (ENTER)."""


def _meta(rpl, cmd):
    """Run a dotted meta-command (`cmd` is the text after the '.').  Returns
    True if the REPL should exit.  Unknown commands print help, never touch the
    stack."""
    parts = cmd.split()
    name = parts[0].lower() if parts else ""
    args = parts[1:]
    if name in ("quit", "exit", "q"):
        return True
    if name in ("help", "h", "?", ""):
        if args and args[0].lower() in ("key", "keys"):
            print(_KEYS_HELP)
        elif args and args[0].lower() in ("char", "chars"):
            _print_chars(rpl)
        else:
            print(_META_HELP)
    elif name == "keys":
        print(_KEYS_HELP)
    elif name in ("chars", "char"):
        _print_chars(rpl)
    elif name == "stack":
        rpl.show(limit=None)
    elif name == "clear":
        rpl.clear(); rpl.show()
    elif name == "lcd":
        ascii_mode = args and args[0].lower() == "ascii"
        print(rpl.emu.lcd_ascii() if ascii_mode else rpl.emu.lcd_braille())
    elif name == "key":
        if not args:
            print("  usage: .key KEY...   names (a-z, 0-9, enter, del, back, "
                  "lshift, rshift, alpha, on, +, -, ...) or hex codes")
        else:
            codes, bad = [], None
            for a in args:
                try:
                    codes.append(_parse_key(a))
                except ValueError:
                    bad = a
                    break
            if bad is not None:
                print("  unknown key %r -- use a key name or a hex code "
                      "(see docs/keymap.md)" % bad)
            else:
                for c in codes:
                    rpl._key(c)            # press + release (a tap)
                rpl._settle(6)             # let the calc redraw
                print(rpl.emu.lcd_braille())
    elif name == "store":
        if not args:
            print("  usage: .store FILE   (write the level-1 object)")
        elif rpl.depth() < 1:
            print("  empty stack")
        else:
            nibs = rpl.emu.read_object(1)
            if not nibs:
                print("  could not read the level-1 object")
            else:
                path = os.path.expanduser(args[0])
                try:
                    with open(path, "wb") as f:
                        f.write(HP48_BIN_HEADER)
                        f.write(_pack_nibbles(nibs))
                except OSError as e:
                    print("  %s" % e)
                else:
                    print("  wrote %s (%d nibbles)" % (path, len(nibs)))
    elif name == "load":
        if not args:
            print("  usage: .load FILE   (push an object, or the file as a string)")
        else:
            path = os.path.expanduser(args[0])
            try:
                with open(path, "rb") as f:
                    data = f.read()
            except OSError as e:
                print("  %s" % e)
            else:
                if data[:7] == b"HPHP48-":         # binary object transfer file
                    nibs = _unpack_nibbles(data[8:])
                    size = _object_nibble_size(nibs)
                    if 5 <= size <= len(nibs):     # trim byte-padding to the
                        nibs = nibs[:size]         # exact object (GC-safe)
                    ok = rpl.emu.push_object(nibs)
                else:                              # treat the file as a string
                    ok = rpl.emu.push_string(data)
                if ok:
                    rpl.show()
                else:
                    print("  could not push the object (out of memory or "
                          "malformed)")
    elif name == "save":
        target = os.path.expanduser(args[0]) if args else rpl.save_dir
        if not target:
            print("  no --state directory; use '.save DIR' to choose one")
        elif rpl.emu.save_state(target) == 0:
            print("  saved state to %s" % target)
        else:
            print("  could not save state to %s" % target)
    else:
        print("  unknown command '.%s' -- '.help' for the list" % name)
    return False


def _init_readline():
    """Best-effort line editing for the REPL: arrow-key editing, persistent
    history, and tab-completion of meta-commands and key names.  A no-op if the
    readline module isn't available (e.g. plain Windows Python)."""
    try:
        import readline
    except ImportError:
        return
    import atexit
    histfile = os.path.join(config_dir(), "calc48_history")
    try:
        readline.read_history_file(histfile)
    except OSError:
        pass
    readline.set_history_length(1000)

    def _save():
        try:
            os.makedirs(config_dir(), exist_ok=True)
            readline.write_history_file(histfile)
        except OSError:
            pass
    atexit.register(_save)

    def _complete(text, state):
        s = readline.get_line_buffer().lstrip()
        if not s.startswith("."):          # only complete meta-commands
            return None
        parts = s.split()
        if len(parts) <= 1 and not s.endswith(" "):
            cands = ["." + c for c in _META_CMDS if ("." + c).startswith(text)]
        elif parts[0] == ".key":
            cands = sorted(n for n in KEY_NAMES if n.startswith(text.lower()))
        elif parts[0] in (".store", ".load", ".save"):
            import glob
            cands = [p + ("/" if os.path.isdir(p) else "")
                     for p in glob.glob(os.path.expanduser(text) + "*")]
        else:
            cands = []
        return cands[state] if state < len(cands) else None

    readline.set_completer_delims(" \t\n")
    readline.set_completer(_complete)
    readline.parse_and_bind("tab: complete")


def _repl(rpl):
    _init_readline()
    print("calc48 -- User RPL.  '.help' for commands.")
    rpl.show()
    while True:
        try:
            line = input("calc48> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line in ("quit", "exit"):       # bare courtesy aliases
            break
        if line.startswith("."):           # meta-command namespace
            if _meta(rpl, line[1:]):
                break
            continue
        res = rpl.eval(line)               # mutates the persistent stack
        if res.error:
            print("  error:", res.error)
        rpl.show()                         # show the resulting stack


def _run_stream(rpl, stream, label="line"):
    """Evaluate each line of `stream`; print the resulting stack at EOF.  On the
    first error, report it to stderr and bail out with a non-zero exit code."""
    for lineno, raw in enumerate(stream, 1):
        line = raw.strip()
        if not line:
            continue
        res = rpl.eval(line)
        if res.error:
            print("calc48: %s %d: %s" % (label, lineno, res.error),
                  file=sys.stderr)
            return 1
    rpl.show(limit=None)
    return 0


def _eval_args(rpl, items):
    """Evaluate command-line RPL args, in order, onto the stack (no output).
    Returns 0, or 1 (with a stderr message) on the first error."""
    for i, src in enumerate(items, 1):
        res = rpl.eval(src)
        if res.error:
            print("calc48: arg %d (%r): %s" % (i, src, res.error),
                  file=sys.stderr)
            return 1
    return 0


def _profile_has_state(d):
    d = os.path.expanduser(d)
    return (os.path.isfile(os.path.join(d, "hp48"))
            and os.path.isfile(os.path.join(d, "ram")))


def main(argv):
    # Input may contain Unicode glyphs (π, √, ...) and output emits Unicode
    # (glyphs, braille); force UTF-8 on both so it works even when piped under a
    # non-UTF-8 locale (Python >= 3.7).
    for _stream in (sys.stdin, sys.stdout):
        try:
            _stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(
        description="Command-line HP 48 RPL calculator (libcalc48).")
    ap.add_argument("-s", "--state", metavar="DIR",
                    help="load the calculator state (hp48 + ram) from DIR "
                         "instead of the embedded blank image; read-only "
                         "unless --save is given")
    ap.add_argument("-w", "--save", action="store_true",
                    help="on clean exit, write the state back to the --state "
                         "DIR (requires --state); use it to build up a profile")
    ap.add_argument("-r", "--rom", metavar="FILE",
                    help="path to the HP 48 ROM (overrides $HP48_ROM and the "
                         "default <config_dir>/hp48/rom)")
    ap.add_argument("file", nargs="?",
                    help="with no piped stdin, run RPL from this file (one "
                         "statement per line); with piped stdin it is instead "
                         "RPL placed on the stack (stdin is the program)")
    ap.add_argument("rpl", nargs="*",
                    help="RPL evaluated onto the stack before the file/stdin "
                         "runs (use `--` before args that start with '-')")
    args = ap.parse_args(argv[1:])

    if args.save and not args.state:
        ap.error("--save requires --state")

    # --state DIR is both the (optional) load source and the save target -- the
    # interactive `.save` command can write to it even without --save.  --save
    # just adds an automatic save on a clean exit.  An empty DIR starts blank
    # (you can populate it and save).
    profile = None
    save_dir = args.state                   # None if --state absent
    auto_save = args.save
    if args.state and _profile_has_state(args.state):
        profile = args.state                # load the existing profile

    rom = os.path.expanduser(args.rom) if args.rom else None
    if rom and not os.path.isfile(rom):
        print("calc48: ROM not found: %s" % rom, file=sys.stderr)
        return 2

    try:
        engine = Rpl(profile=profile, save_dir=save_dir, rom=rom)
    except RomNotFoundError as e:
        print(e.message, file=sys.stderr)
        return 2
    except (OSError, RuntimeError) as e:
        print("calc48: %s" % e, file=sys.stderr)
        return 2

    with engine as rpl:
        if not sys.stdin.isatty():
            # Piped/redirected input is the program: stdin is the filter, and
            # ALL positional args are RPL placed on the stack first.
            pre = ([args.file] if args.file is not None else []) + args.rpl
            rc = _eval_args(rpl, pre)
            if rc == 0:
                rc = _run_stream(rpl, sys.stdin)
        else:
            # No piped input: the first arg (if any) is a file to run, with any
            # further args placed on the stack first; otherwise a REPL.
            rc = _eval_args(rpl, args.rpl)
            if rc == 0 and args.file is not None:
                try:
                    fp = open(args.file, encoding="utf-8")
                except OSError as e:
                    print("calc48: %s" % e, file=sys.stderr)
                    rc = 2
                else:
                    with fp:
                        rc = _run_stream(rpl, fp)
            elif rc == 0:
                _repl(rpl)
        # Auto-save (only with --save) on a clean run, so an aborted run can't
        # corrupt the profile.  (The REPL's `.save` is separate and explicit.)
        if auto_save and rc == 0:
            if rpl.save() != 0:
                print("calc48: failed to save state to %s" % save_dir,
                      file=sys.stderr)
                rc = 1
            else:
                print("calc48: saved state to %s" % save_dir, file=sys.stderr)
        return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))
