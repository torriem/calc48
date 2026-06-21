#!/usr/bin/env python3
r"""
calc48.py -- a command-line HP 48 RPL calculator (built on libcalc48).

Two modes, chosen automatically:
  * Piped/redirected input: each line of stdin is evaluated, then the resulting
    stack is printed.  The first error aborts with a message on stderr and a
    non-zero exit code.
        echo "2 3 + 4 *" | python3 examples/calc48.py        # -> 1: 20
        python3 examples/calc48.py < script.rpl
  * A terminal (no piped input): an interactive REPL with a "calc48>" prompt
    (commands: stack, clear, quit).
        python3 examples/calc48.py

It needs the HP 48 ROM -- found via $HP48_ROM, else <config_dir>/hp48/rom -- and
the embedded blank state (build it once with tools/make_blank_state.py).  If the
ROM is missing it prints where to download and place it.

By default every run is a pristine, independent calculator and nothing is saved.
To work in a persistent *profile* (its own hp48 + ram, separate from the ROM):

    -s/--state DIR   load the state (hp48 + ram) from DIR instead of the blank
                     image -- read-only by default
    -w/--save        on clean exit, write the state back to that DIR (requires
                     --state); the way to build up / update a profile

So `--state DIR` alone runs an existing profile read-only; `--state DIR --save`
loads it (or starts blank if DIR has no state yet) and persists changes on exit.
In filter mode the save is skipped if the run aborts on an error.

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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hp48 import Hp48          # noqa: E402
# _blank_state (the embedded blank GX RAM/CPU image) is imported lazily in
# __init__ -- it is ROM-derived and generated locally by tools/make_blank_state.py
# rather than committed, so rpl_eval must import cleanly without it.

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
        """Replace ASCII digraphs (<<, >>, ->, ...) and HP 48 backslash escapes
        (\\pi, \\GS, \\oo, ...) with the HP 48 single characters, leaving the
        contents of "..." string literals untouched.  Longest match wins, so
        >> vs >=, and \\<< vs \\<, don't collide."""
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
        """The calculator's representation of a level (string quoted, « », ...).
        Binary integers and algebraics go through →STR so they show in the live
        base / as infix, matching the real calc."""
        prolog = self.emu._lib.hp48_object_prolog(
            self.emu._lib.hp48_stack_addr(level))
        if prolog in (DOHSTR, DOSYMB):
            s = self._calc_str(level)
            if s is not None:
                return s
        t = self._describe(level)
        t = t.split("] ", 1)[1] if "] " in t else t
        return t.replace("\\<<", "«").replace("\\>>", "»").replace("\\->", "→")

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


def _repl(rpl):
    print("calc48 -- User RPL.  Commands: stack, clear, quit.")
    rpl.show()
    while True:
        try:
            line = input("calc48> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if line in ("quit", "exit"):
            break
        if line == "clear":
            rpl.clear(); rpl.show(); continue
        if line == "stack":
            rpl.show(limit=None); continue
        if not line:
            continue
        res = rpl.eval(line)               # mutates the persistent stack
        if res.error:
            print("  error:", res.error)
        rpl.show()                         # show the resulting stack


def _filter(rpl):
    """Evaluate each line of stdin; print the resulting stack at EOF.  On the
    first error, report it to stderr and bail out with a non-zero exit code."""
    for lineno, raw in enumerate(sys.stdin, 1):
        line = raw.strip()
        if not line:
            continue
        res = rpl.eval(line)
        if res.error:
            print("calc48: line %d: %s" % (lineno, res.error), file=sys.stderr)
            return 1
    rpl.show(limit=None)
    return 0


def _profile_has_state(d):
    d = os.path.expanduser(d)
    return (os.path.isfile(os.path.join(d, "hp48"))
            and os.path.isfile(os.path.join(d, "ram")))


def main(argv):
    ap = argparse.ArgumentParser(
        description="Command-line HP 48 RPL calculator (libcalc48).")
    ap.add_argument("-s", "--state", metavar="DIR",
                    help="load the calculator state (hp48 + ram) from DIR "
                         "instead of the embedded blank image; read-only "
                         "unless --save is given")
    ap.add_argument("-w", "--save", action="store_true",
                    help="on clean exit, write the state back to the --state "
                         "DIR (requires --state); use it to build up a profile")
    args = ap.parse_args(argv[1:])

    if args.save and not args.state:
        ap.error("--save requires --state")

    profile = save_dir = None
    if args.state:
        if _profile_has_state(args.state):
            profile = args.state           # load the existing profile
        elif not args.save:
            ap.error("no hp48/ram in %s (use --save to create a profile there)"
                     % args.state)
        # else: bootstrap a new profile from the embedded blank, save on exit
        if args.save:
            save_dir = args.state

    try:
        engine = Rpl(profile=profile, save_dir=save_dir)
    except RomNotFoundError as e:
        print(e.message, file=sys.stderr)
        return 2
    except (OSError, RuntimeError) as e:
        print("calc48: %s" % e, file=sys.stderr)
        return 2

    with engine as rpl:
        if sys.stdin.isatty():             # a terminal -> interactive REPL
            _repl(rpl)
            rc = 0
        else:                              # piped/redirected input -> filter
            rc = _filter(rpl)
        # Persist only on a clean run, so an aborted filter can't corrupt the
        # profile.
        if save_dir and rc == 0:
            if rpl.save() != 0:
                print("calc48: failed to save state to %s" % save_dir,
                      file=sys.stderr)
                rc = 1
            else:
                print("calc48: saved state to %s" % save_dir, file=sys.stderr)
        return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))
