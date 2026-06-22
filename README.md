# calc48 / libcalc48

A refactor of **x48** — the classic HP 48 SX/GX calculator emulator — that
splits the emulator into a self-contained, embeddable C library and keeps the
SDL app as one client of it. Around the library are a command-line calculator, a
Python binding, and assorted examples and tools.

The core (`libcalc48`) is plain C with **no SDL, no UI toolkit, and no built-in
filesystem/serial/terminal dependency**: everything the host provides — display,
storage, serial transport, the clock — goes through small callback interfaces.
The same library drives the SDL app, a Python `ctypes` binding, the `calc48`
CLI, an Android/Qt build, or a headless test harness.

## Layout

| path | what it is |
|---|---|
| `libcalc48/` | the emulator core (C library) + its Python binding `hp48.py` — see `libcalc48/README.md` |
| `calc48/` | `calc48.py`, a command-line / REPL RPL calculator — see `calc48/README.md` |
| `sdl/` | the SDL 1.2 front end (the original x48-sdl `x48` app), one client of the library |
| `examples/` | small programs showing how to drive the library (below) |
| `tools/` | developer utilities (ROM/state/font helpers; some legacy x48 tools) |
| `docs/` | reference docs (`keymap.md`, `stack_io_plan.md`, …) |

## Building

CMake (builds the library both static and shared, and the SDL `x48` app):

```sh
cmake -S . -B build
cmake --build build
```

Artifacts land in `build/` — `build/libcalc48.{a,so}` and `build/x48`. The
library's build options (e.g. compiling out the bundled storage/serial/debugger
providers for an embedded build) are documented in `libcalc48/README.md`.

The SDL app and `calc48` need an HP 48 ROM, which is **not** included (it is
HP-copyrighted); download it from <https://www.hpcalc.org/details/4524>.

## Examples

Driving the library without SDL (each is dependency-free):

- `examples/ffi_smoke.c` — minimal headless embedding in C; links only
  `libcalc48` and exercises create / get_lcd / press_key / destroy. Built by
  CMake as `hp48_ffi_smoke`.
- `examples/rpl_eval.py` — use the emulator as a **User RPL scripting engine**:
  feed source text, get the resulting stack back as Python values; includes a
  REPL. (`calc48/calc48.py` is the polished command-line front end built on the
  same idea.)
- `examples/calc48_skeleton.py` — the smallest `ctypes` bring-up: load `~/.hp48`
  with the bundled stdio provider and start the emulator.

The Python binding itself (`Hp48`, a `ctypes` wrapper with a braille/ASCII LCD
renderer) lives with the library at `libcalc48/hp48.py`.

## Tools

- `tools/make_blank_state.py` — generate the embedded blank-state image
  `calc48`/`rpl_eval` boot from (ROM-revision specific; not committed).
- `tools/extract_fonts.py` — dump the HP 48 GX system fonts via `→GROB`.
- plus legacy x48 utilities (`mkcard`, `dump2rom`, `checkrom`, …).

See `tools/README.md`.

## License

GPL v2 or later, inherited from x48. See the headers in each source file.
