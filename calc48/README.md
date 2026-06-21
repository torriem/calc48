# calc48 — a command-line HP 48 RPL calculator

`calc48.py` is a small command-line calculator built on **libcalc48**: it drives
a real HP 48 emulator (the calculator's own RPL parser/evaluator), so you get
the full User RPL language, not a reimplementation. Use it as a CLI calculator,
a scriptable RPL engine, or an interactive REPL.

## Requirements

- **libcalc48** built with the stack API (`HP48_WITH_STACK_IO`, the default) —
  `calc48.py` imports the Python binding `libcalc48/hp48.py` and finds
  `libcalc48.so` via `$HP48_LIB` or `../build/libcalc48.so`.
- **The HP 48 ROM** (HP-copyrighted, *not* bundled). calc48 looks for it via
  `--rom`, then `$HP48_ROM`, then `<config_dir>/hp48/rom` (e.g.
  `~/.config/hp48/rom` on Linux). If it's missing, calc48 prints where to
  download (<https://www.hpcalc.org/details/4524>) and where to put it.
- **The embedded blank state** — generated once into `libcalc48/_blank_state.py`
  by `tools/make_blank_state.py` (a cold boot from zeroed RAM doesn't reach a
  usable state headlessly, so a small ROM-derived blank snapshot is needed). It
  is ROM-revision specific; regenerate it if you change ROMs.

## Input modes

Chosen automatically; **piped/redirected stdin always wins** — if it's present,
stdin is the program and every positional argument is RPL placed on the stack.
Otherwise (stdin is a terminal) the first argument, if any, is a file to run.

| invocation | behavior |
|---|---|
| `echo "2 3 + 4 *" \| calc48.py` | piped stdin is the program; prints the stack (`1: 20`) |
| `echo "+" \| calc48.py 3 4` | piped stdin + args: args are pushed first (→ `1: 7`) |
| `calc48.py script.rpl` | terminal + file arg: run the file, print the stack |
| `calc48.py script.rpl 3 4` | file arg + extra args pushed onto the stack first |
| `calc48.py < script.rpl` | run a file non-interactively (redirect = filter mode) |
| `calc48.py` | terminal, no file: interactive REPL (`.help` for commands) |

A file/stdin run aborts on the first error (message on stderr, non-zero exit).

## Options

| option | meaning |
|---|---|
| `-r, --rom FILE` | ROM path; overrides `$HP48_ROM` and the default `<config_dir>/hp48/rom` |
| `-s, --state DIR` | load the state (`hp48`+`ram`) from a profile dir instead of the blank image; also the save target |
| `-w, --save` | automatically write the state back to the `--state` dir on a clean exit (requires `--state`) |

Without `--state` every run is a pristine, independent calculator and nothing is
saved. `--state DIR` loads a profile (or starts blank if the dir has none yet)
without auto-saving — read-only unless you explicitly `.save`. `--state DIR
--save` also persists on a clean exit (skipped if a run aborts on an error). So
you can build up a working profile and reuse it read-only.

## Input conveniences

Translated to HP 48 characters before compiling (both skip the inside of `"..."`
string literals):

- **ASCII digraphs:** `<<` `>>` `->` `<=` `>=` `!=` → `«` `»` `→` `≤` `≥` `≠`.
- **HP 48 backslash escapes** (what `→STR` / ASCII transfer use): `\pi`→π,
  `\GS`→Σ, `\Gd`→δ, `\oo`→∞, … — e.g. `2 \pi * ->NUM`.
- **Typed Unicode glyphs** — you can also just type/paste the real characters
  (`π`, `√`, `Σ`, `→`, `≤`, «», …) and they're mapped to the HP 48 character,
  including inside `"..."` strings. (stdin/files are read as UTF-8.)

Binary integers and algebraics are displayed via the calculator's own `→STR`, so
they show in the live base (BIN/OCT/DEC/HEX) and as infix. Conversely, when the
stack is printed (all modes), HP 48 special characters in strings/algebraics/
programs are shown as their Unicode glyphs (π, Σ, √, «», …) rather than the
`\pi`-style spellings. `.chars` lists the spellings and their glyphs.

## REPL meta-commands

In the interactive REPL a line starting with `.` is a calc48 command (not RPL);
the stack persists between lines. An unknown `.` command prints the list rather
than touching the stack.

| command | action |
|---|---|
| `.stack` | show the whole stack |
| `.clear` | empty the stack |
| `.lcd [ascii]` | show the calculator screen as braille (or `ascii`) |
| `.key KEY...` | tap key(s) by name or hex matrix code, then show the screen |
| `.keys` | list the `.key` names (`.help keys` too) |
| `.chars` | list the ASCII spellings for HP 48 special characters (e.g. `\v/`=√, `\pi`=π) |
| `.store FILE` | write the level-1 object to `FILE` (HP binary object format) |
| `.load FILE` | push an object from `FILE` (a non-HP48 file loads as a string) |
| `.save [DIR]` | save the whole calculator state to `DIR`, else the `--state` dir |
| `.reset` | reboot: reload the `--state` profile if it has saved state, else the blank image |
| `.reset_all` | reboot from the embedded blank image, ignoring `--state` |
| `.help` | list the commands |
| `.quit` | exit (bare `quit`/`exit` also work) |

`.store`/`.load` use the HP 48 **binary object transfer format** (an `HPHP48-`
header + the object's nibbles, two per byte) — the same format droid48, x48,
Emu48, Conn, and a real HP 48 read and write, so objects move freely between
them. (`.store` saves one stack object to a file; `.save` persists the whole
calculator state to a directory — different things.)

If the Python `readline` module is available, the REPL also has line editing,
persistent history (`<config_dir>/hp48/calc48_history`), and tab-completion of
meta-commands, `.key` names, and filenames (`.store`/`.load`/`.save`).

**`.key` names** follow `../docs/keymap.md`: digits `0`–`9`, letters `a`–`z`
(HP 48 alpha layout — combine with `alpha` to type letters), and named
keys/aliases (`enter`, `del`, `back`, `lshift`, `rshift`, `alpha`, `on`, arrows
`up`/`down`/`left`/`right`, `+ - * /`, `sto`, `eval`, `sin`/`cos`/`tan`, …); an
unknown token falls back to a hex code. E.g. `.key 1 2 enter` (→ `12`),
`.key rshift sin` (ASIN).

## Examples

```sh
# arithmetic
echo "355 113 / ->NUM" | python3 calc48/calc48.py

# a script that leaves a result on the stack
printf '1 10 FOR i i SQ NEXT 10 ->LIST\n' | python3 calc48/calc48.py

# build a profile, then reuse it read-only
printf "5 'A' STO\n" | python3 calc48/calc48.py --state ~/myprofile --save
echo "A 3 *" | python3 calc48/calc48.py --state ~/myprofile      # -> 15
```

See `../README.md` for the project as a whole and `../libcalc48/README.md` for
the library and its Python binding.
