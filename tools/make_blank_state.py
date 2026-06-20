#!/usr/bin/env python3
"""
make_blank_state.py -- (re)generate examples/_blank_state.py for rpl_eval.

rpl_eval boots a pristine calculator from a ROM (supplied by the user) plus an
*embedded* blank RAM/CPU image, because a cold boot from zeroed RAM doesn't reach
a usable state headlessly.  That blank image is ROM-revision specific, so if your
ROM differs from the one it was built with, regenerate it here.

It takes a *seed* saved-state directory (a working calculator of the same ROM
revision), clears the stack and purges all HOME variables to make it blank, then
writes the `hp48` (CPU/peripheral state) and `ram` blobs -- gzip+base64 -- into
examples/_blank_state.py.  The ROM itself is never written (it's the user's).

Usage:
    HP48_LIB=build/libcalc48.so python3 tools/make_blank_state.py [SEED_STATE_DIR]

SEED_STATE_DIR defaults to ~/.hp48.
"""

import base64
import gzip
import importlib.util
import os
import sys
import tempfile
import textwrap

HERE = os.path.dirname(os.path.abspath(__file__))
EXAMPLES = os.path.join(HERE, "..", "examples")
OUT = os.path.join(EXAMPLES, "_blank_state.py")


def _load_rpl():
    spec = importlib.util.spec_from_file_location(
        "rpl_eval", os.path.join(EXAMPLES, "rpl_eval.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.Rpl


def main(argv):
    seed = argv[1] if len(argv) > 1 else "~/.hp48"
    Rpl = _load_rpl()
    rpl = Rpl(state_dir=seed)               # load full seed state, clear stack
    rpl.eval("HOME VARS PURGE")             # purge all HOME vars (no-op if none)
    for _ in range(30):
        rpl.emu.run_slice(20000)            # settle to a clean idle point
    td = tempfile.mkdtemp()
    if rpl.emu.save_state(td) != 0:
        sys.exit("save_state failed")
    rpl.close()

    def blob(name):
        raw = open(os.path.join(td, name), "rb").read()
        return base64.b64encode(gzip.compress(raw, 9)).decode()

    def wrap(s):
        return "\n".join('    "%s"' % c for c in textwrap.wrap(s, 72))

    body = ('"""\n'
            "Auto-generated blank HP 48 *GX* state for rpl_eval.py "
            "(tools/make_blank_state.py).\n"
            "gzip+base64 of the `hp48` (CPU/peripheral registers) and `ram` files "
            "from a\nfreshly cleared GX calculator.  NOT the ROM (HP-copyrighted; "
            "supplied\nseparately).  ROM-revision specific.\n"
            '"""\n\n'
            "HP48_GZ_B64 = (\n%s\n)\n\n"
            "RAM_GZ_B64 = (\n%s\n)\n" % (wrap(blob("hp48")), wrap(blob("ram"))))
    with open(OUT, "w") as f:
        f.write(body)
    print("wrote", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
