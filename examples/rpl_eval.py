#!/usr/bin/env python3
"""
rpl_eval.py -- use the HP 48 emulator as a User RPL scripting engine.

This turns libcalc48 into a callable RPL interpreter: you hand it User RPL
*source text*, it drives the calculator's own parser (the `STR→` command) to
compile and run it, and hands back the full stack plus any error.

    from rpl_eval import Rpl
    rpl = Rpl()                       # boots ~/.hp48 (any SX or GX state)

    res = rpl.eval("2 3 * 10 +")     # Result(stack=[16.0], error=None)
    res.stack[0]                     # 16.0   ("the result" = top of stack)
    res.error                        # None

    rpl.eval('"abc" 5 /').error      # RplError('Bad Argument Type', '# 202h')
    rpl.eval1("2 3 +")               # 5.0    (convenience: top item, or raises)

eval() returns the FULL stack (decoded, top first) so the caller can take the
top item as its result or inspect the rest; errors are returned in .error
(not raised).  The stack persists between calls (it's a live calculator).

ASCII digraphs are translated to HP 48 characters so you can type plain ASCII:
`<<`->`«`, `>>`->`»`, `->`->`→`, `<=`->`≤`, `>=`->`≥`, `!=`->`≠` (see
Rpl.DIGRAPHS; extend it as you like).  Translation skips the inside of "..."
string literals, and you can disable it per call with eval(src, translate=False).

How it works: the source is pushed as a string object (hp48_push_string), then
`STR→` is run by key injection (the calc's parser, version-independent), and the
resulting stack is read back (hp48_stack_describe / hp48_read_object).  `STR→`
*evaluates* the source -- so a bare program literal `« … »` comes back compiled
but unexecuted, while an expression runs and leaves its results.

Requires libcalc48 built with HP48_WITH_STACK_IO (the default).  GX or SX both
work.  Run directly for a REPL:

    python3 examples/rpl_eval.py                 # interactive REPL
    python3 examples/rpl_eval.py "2 3 + 4 *"     # one-shot
"""

import ctypes
import os
import sys
from collections import namedtuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hp48 import Hp48  # noqa: E402

# What eval() returns: the FULL stack (decoded, top first -> stack[0] is the
# top/result) and the error (an RplError or None).  So a caller can always do
# res.stack[0] for "the result", inspect the rest, and check res.error.
Result = namedtuple("Result", "stack error")

# Object prologs (see libcalc48/include/rpl.h)
DOREAL, DOEREL, DOCSTR = 0x02933, 0x02955, 0x02a2c

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

    def __init__(self, state_dir="~/.hp48", lib=None):
        self.emu = Hp48(lib)
        if not self.emu._have_stack:
            raise RuntimeError("libcalc48 built without HP48_WITH_STACK_IO")
        if self.emu.load_state(os.path.expanduser(state_dir)) != 0:
            raise RuntimeError("could not load state from %s" % state_dir)
        self.emu.start()
        self.emu.display_init()
        self._settle(20)
        self.clear()                       # start from an empty stack

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

    def _decode(self, level):
        """Return a Python value for the object at `level`:
        Real -> float, String -> str, everything else -> its RPL text."""
        addr = self.emu._lib.hp48_stack_addr(level)
        prolog = self.emu._lib.hp48_object_prolog(addr)
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
        """Replace ASCII digraphs (<<, >>, ->, ...) with the HP 48 single
        characters, leaving the contents of "..." string literals untouched.
        Longest match wins, so >> and >= don't collide."""
        keys = sorted(self.DIGRAPHS, key=len, reverse=True)
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
                        out.append(chr(self.DIGRAPHS[k])); i += len(k); break
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
        """The calculator's representation of a level (string quoted, « », ...)."""
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


def _demo(rpl):
    for src in ('2 3 * 10 +', '"abc" DUP SIZE',
                '<< DUP * >> 9 SWAP EVAL',   # ASCII << >> -> « »
                '1 2 3 3 ->LIST',            # ASCII -> -> →
                '3 5 <=  4 4 !=',            # ASCII <= != -> ≤ ≠
                '"abc" 5 /',                 # runtime error: Bad Argument Type
                '5 0 /',                     # runtime error: Infinite Result
                '1 2 )'):                    # syntax error
        rpl.clear()                        # independent line; show its own stack
        res = rpl.eval(src)
        if res.error:
            print("  %-22s -> error: %s" % (src, res.error))
        else:
            print("  %-22s -> %r" % (src, res.stack))


def main(argv):
    with Rpl() as rpl:
        if len(argv) > 1:                  # one-shot: evaluate the argument
            res = rpl.eval(argv[1])
            if res.error:
                print("error:", res.error); return 1
            print(res.stack)
            return 0

        print("RPL scripting demo (emulator as interpreter):")
        _demo(rpl)

        if sys.stdin.isatty():             # interactive REPL
            print("\nREPL -- User RPL (ASCII << >> -> ok); 'stack' / 'clear' / "
                  "'quit'.  The stack persists between lines.")
            rpl.show()
            while True:
                try:
                    line = input("rpl> ").strip()
                except (EOFError, KeyboardInterrupt):
                    print(); break
                if line in ("quit", "exit"):
                    break
                if line == "clear":
                    rpl.clear(); rpl.show(); continue
                if line == "stack":
                    rpl.show(limit=None); continue
                if not line:
                    continue
                res = rpl.eval(line)        # mutates the persistent stack
                if res.error:
                    print("  error:", res.error)
                rpl.show()                  # show the resulting stack
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
