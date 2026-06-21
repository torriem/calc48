# libcalc48

A self-contained, embeddable **HP 48 SX/GX emulator core** in plain C.

`libcalc48` is the emulator extracted from the SDL app *x48*: the Saturn CPU,
memory, display controller, timers, and serial UART, with **no SDL, no UI
toolkit, and no built-in filesystem or terminal dependency**. Everything the
host must provide — rendering, storage, serial transport, and (optionally) the
clock — is supplied through small plain-C callback tables. The same library
drives the bundled SDL front end, a Python `ctypes` binding, a Qt app, an
Android NDK build, or a headless test harness.

The C API keeps the `hp48_*` names because it emulates the HP 48 device.

> History/design notes live in `../REFACTOR_PLAN.md`; this file is the usage
> reference.

## Features

- **Instance-based.** State lives in an `hp48_t`; create/destroy as many as you
  like (see the threading caveat below).
- **Cooperative scheduling.** The *host* owns the event loop and calls
  `hp48_run_slice()`; the library never blocks or installs signal handlers.
- **SX and GX**, auto-detected from the ROM at load time.
- **Callback-based host integration** for display (`hp48_ui_t`), storage
  (`hp48_io_t`), serial transport (`hp48_serial_t`), and the clock.
- **RPL stack read/write API** — inspect or inject objects on the user stack.
- **Builds static and shared**, depends only on libc.

## Building

CMake (this directory is a subproject of the top-level build, or can be built
standalone):

```sh
cmake -S . -B build
cmake --build build
```

Targets:

| Target          | Output             | Use                               |
|-----------------|--------------------|-----------------------------------|
| `calc48`        | `libcalc48.a`      | static link (the SDL app, tests)  |
| `calc48_shared` | `libcalc48.so`     | FFI consumers (Python, Qt, …)     |

### Build options

All `ON` by default; turn off for a minimal/embedded build. With them all off
the core performs no filesystem, serial, or terminal I/O (see *Caveats*).

| Option                | Default | What it adds                                                        |
|-----------------------|---------|---------------------------------------------------------------------|
| `HP48_WITH_STDIO_IO`  | ON      | Bundled `stdio` storage provider (`<dir>/<name>` files)             |
| `HP48_WITH_SERIAL`    | ON      | Bundled serial providers (PTY/device + socket)                      |
| `HP48_WITH_STACK_IO`  | ON      | RPL user-stack read/write API (`hp48_rpl.h`)                        |
| `HP48_WITH_DEBUGGER`  | ON      | Interactive stdin debugger (else an inert stub is compiled)         |

```sh
# Minimal embeddable core: host supplies everything via callbacks
cmake -S . -B build -DHP48_WITH_STDIO_IO=OFF -DHP48_WITH_SERIAL=OFF \
                    -DHP48_WITH_DEBUGGER=OFF
```

## Concepts

- **The active instance.** A process-global pointer (`cpu`) selects which
  `hp48_t` the flat API acts on. `hp48_create()` allocates one and makes it
  active; `hp48_set_active()` switches. Most calls take no handle — they act on
  the active instance.
- **Run slices.** `hp48_run_slice(budget_us)` executes instructions for up to
  `budget_us` of real time, then returns so you can pump your UI. Call it from a
  timer/loop (~50–60 Hz is plenty). It returns a status telling you what to do
  next.
- **Callbacks, not policy.** The core never opens files, sockets, or windows on
  its own (unless you install a bundled provider). You hand it function tables.

## Quick start (C)

```c
#include "hp48.h"
#include "hp48_rpl.h"

int main(void) {
    hp48_t *h = hp48_create();              /* allocate + make active */

    /* Load a saved calculator from a directory (needs HP48_WITH_STDIO_IO).
       The dir holds: rom, hp48, ram, port1, port2 (same files x48 uses). */
    if (hp48_load_state("/home/me/.hp48") != 0)
        hp48_init_from_rom("/home/me/.hp48/rom");   /* fresh boot instead */

    hp48_start();            /* one-time run setup */
    hp48_display_init();     /* prime the LCD buffer for pull-based hosts */

    for (int frame = 0; frame < 50; frame++) {      /* ~1s; host owns loop */
        int st = hp48_run_slice(20000);             /* 20 ms budget */
        if (st == HP48_HALTED) { /* idle; wait for input or just continue */ }
    }

    hp48_press_key(0x13);    /* press '1' (see docs/keymap.md); 0x8000 = ON */
    hp48_release_key(0x13);

    hp48_save_state("/home/me/.hp48");   /* persist back (optional) */
    hp48_destroy(h);
    return 0;
}
```

Link: `cc myapp.c libcalc48.a` (add `-I libcalc48/include`).

A complete `ctypes` binding is in `../examples/hp48.py`; a minimal headless
embedding check is `../examples/ffi_smoke.c`.

## API reference

Include `hp48.h` for the core API; the callback interfaces and the stack API
have their own headers.

### Lifecycle
```c
hp48_t *hp48_create(void);          /* allocate, init defaults, make active   */
void    hp48_destroy(hp48_t *h);    /* free (frees rom/ram/ports it owns)      */
void    hp48_set_active(hp48_t *h); /* select the instance the API acts on     */
```

### Loading and persistence
Paths are explicit; `0` on success, `<0` on error.
```c
int hp48_load_state(const char *dir);     /* load rom/hp48/ram/port* from dir  */
int hp48_save_state(const char *dir);     /* write the savable set back to dir */
int hp48_init_from_rom(const char *path); /* fresh boot from a ROM image       */
int hp48_load_rom(const char *path);      /* just (re)load a ROM image         */
```
`hp48_load_state`/`save_state` require the bundled `stdio` provider
(`HP48_WITH_STDIO_IO`). Without it, install your own storage callbacks
(`hp48_io.h`) — every resource is moved as an opaque byte blob, so the core
needs no path-based file access (good for Android asset managers, sandboxes,
WASM). The ROM is **not** shipped with the library; you supply it.

### Running
```c
void hp48_start(void);            /* one-time setup before the first slice     */
int  hp48_run_slice(int us);      /* run ~us of real time; returns a status:   */
                                  /*   HP48_RUNNING - budget elapsed, keep going*/
                                  /*   HP48_HALTED  - SHUTDN light-sleep, idle  */
                                  /*   HP48_DEBUG   - debugger tripped          */
int  hp48_check_wakeup(void);     /* used internally for the HALTED state       */
int  hp48_step(void);             /* single instruction (debug/REPL use)        */
```
On `HP48_HALTED` the calculator is asleep waiting for input; you can stop
calling `run_slice` until a key arrives (cuts idle CPU to near zero) or just
keep calling it cheaply.

### Input
```c
void hp48_press_key(int code);    /* HP48 key-matrix code; ON = 0x8000          */
void hp48_release_key(int code);
```
Codes and the PC-keyboard mapping the SDL app uses are in `../docs/keymap.md`
(e.g. `'1'` = `0x13`, `ENTER` = `0x44`, `A` = `0x14`).

### Display
Install rendering callbacks, or pull the LCD buffer:
```c
#include "hp48_ui.h"
void hp48_set_ui(const hp48_ui_t *ui);    /* draw_nibble/draw_annunc/...        */

void hp48_display_init(void);              /* activate + prime the LCD buffer    */
const unsigned char *hp48_get_lcd(int *rows, int *row_stride);
```
`hp48_get_lcd` returns the live nibble buffer (one nibble per byte; each nibble
is 4 horizontal pixels, LSB = leftmost). Headless hosts call
`hp48_display_init()` once so updates are produced even without a window.

### Serial (optional)
```c
#include "hp48_serial.h"
void hp48_set_serial(const hp48_serial_t *s);   /* your transport             */
void hp48_serial_pty(int use_wire, const char *ir_device, int verbose);
void hp48_serial_socket(const char *wire_spec, const char *ir_spec);
```
The UART emulation is always in the core; only the *transport* is a callback.
Bundled providers (with `HP48_WITH_SERIAL`): a PTY/device provider and a socket
provider (`listen:PORT`, `connect:HOST:PORT`, `unix:PATH`, `unix-listen:PATH`).
Two channels exist: wire and IR.

### Clock (optional override)
```c
void hp48_set_clock(uint64_t (*now_us)(void *user), void *user);
```
By default the core reads `gettimeofday()`. Install a microsecond clock to run
on a platform without `gettimeofday` (e.g. MSVC Windows) or to drive
virtual/deterministic time in tests. Pass `NULL` to restore the default.
Process-global.

### RPL user stack (optional, `HP48_WITH_STACK_IO`)
```c
#include "hp48_rpl.h"
int      hp48_stack_depth(void);                 /* objects on the stack       */
uint32_t hp48_stack_addr(int level);             /* level 1 = top              */
uint32_t hp48_object_prolog(uint32_t addr);      /* 5-nibble type id           */
int      hp48_object_size(uint32_t addr);        /* total nibble length        */
int      hp48_read_object(uint32_t addr, unsigned char *out, int max);
int      hp48_stack_describe(int level, char *out, int max);   /* decoded text */

int      hp48_stack_push_object(const unsigned char *obj, int n);  /* raw      */
int      hp48_push_real(double v);               /* typed convenience          */
int      hp48_push_string(const char *bytes, int len);
```
Read objects off, or push objects onto, the user stack — a data path besides the
plug-in ports. Objects are raw nibbles (one per byte, prologue first), so an
object read from one calc can be pushed onto another. Both SX and GX are
supported (chosen at runtime); HP49 is not. See `../docs/stack_io_plan.md`.

## Caveats

- **One active instance / not thread-safe.** The flat API acts on a single
  process-global active instance. You can hold several `hp48_t` and switch with
  `hp48_set_active()`, but **do not** drive two instances concurrently from
  different threads — they share the active pointer (and a few module statics:
  the serial receive buffer, the debugger, the clock hook). One emulator per
  thread with external locking if you must.
- **Safe points for stack I/O.** Call the `hp48_rpl.h` functions only when the
  calc is idle — between `hp48_run_slice()` calls, not mid-slice. Pushing
  mutates live memory-manager pointers; doing it while the CPU runs can corrupt
  the heap. (Running a push in the window between `hp48_load_state()` and
  `hp48_start()` is also safe, since the CPU hasn't started.)
- **No garbage collection on push.** A push fails cleanly (returns `<0`) if the
  contiguous free area is too small, even when a GC would have freed room.
- **HP49 unsupported** by the stack API (`opt_gx >= 2` fails safe).
- **Remaining host couplings in the bare core.** Even with every `HP48_WITH_*`
  off there is no filesystem/serial/terminal I/O, but two libc couplings remain:
  the clock (`gettimeofday`/`time`/`localtime`, overridable via
  `hp48_set_clock`) and the in-memory state serializers `fmemopen` /
  `open_memstream` (POSIX-2008 memory streams — present on Linux/macOS/Android;
  an MSVC build needs a shim).
- **The debugger is a singleton** development tool that reads stdin. Compile it
  out (`HP48_WITH_DEBUGGER=OFF`) for embedded/headless use; the breakpoint hook
  then no-ops and debug events are ignored.
- **Sound is not emulated** (the beeper bit is dropped). If needed it belongs as
  a UI-style callback.
- **You supply the ROM.** It is loaded but never written back by
  `hp48_save_state`.

## License

GPL v2 or later (inherited from x48). See the headers in each source file.

## Command-line calculator (`examples/calc48.py`)

A small RPL calculator built on the library — handy for trying things out and as
a worked example of driving libcalc48 from Python. It needs the HP 48 ROM (it is
*not* bundled — see below) and the embedded blank state (built once with
`tools/make_blank_state.py`).

**Input modes** (auto-selected; piped stdin always wins):

| invocation | behavior |
|---|---|
| `echo "2 3 + 4 *" \| calc48` | piped stdin is the program; prints the stack (`1: 20`) |
| `echo "+" \| calc48 3 4` | piped stdin + args: args are pushed first (→ `1: 7`) |
| `calc48 script.rpl` | terminal + file arg: run the file, print the stack |
| `calc48 script.rpl 3 4` | file arg + extra args pushed onto the stack first |
| `calc48 < script.rpl` | run a file non-interactively (redirect = filter mode) |
| `calc48` | terminal, no file: interactive REPL (`.help` for commands) |

A file/stdin run aborts on the first error (message on stderr, non-zero exit).

**Options:**

| option | meaning |
|---|---|
| `-r, --rom FILE` | ROM path; overrides `$HP48_ROM` and the default `<config_dir>/hp48/rom` |
| `-s, --state DIR` | load the state (`hp48`+`ram`) from a profile dir instead of the blank image; read-only by default |
| `-w, --save` | on a clean exit, write the state back to the `--state` dir (requires `--state`) — the way to build up a profile |

The ROM is found via `--rom`, then `$HP48_ROM`, then `<config_dir>/hp48/rom`
(`~/.config/hp48/rom` on Linux); if missing, calc48 prints where to download and
place it. Without `--state` every run is a pristine, independent calculator and
nothing is saved. `--state DIR` alone runs a profile read-only; `--state DIR
--save` loads it (or starts blank if the dir has no state yet) and persists on a
clean exit — the save is skipped if a run aborts on an error.

ASCII input conveniences are translated to HP 48 characters: digraphs `<<` `>>`
`->` `<=` `>=` `!=` → `« » → ≤ ≥ ≠`, and backslash escapes like `\pi` `\GS`
`\oo` (what `→STR` / ASCII transfer use). Binary integers and algebraics are
displayed via the calc's own `→STR`, so they show in the live base and as infix.

**REPL meta-commands.** In the interactive REPL, a line that starts with `.` is
a calc48 command (not RPL); the stack persists between lines. An unknown `.`
command prints the list instead of touching the stack.

| command | action |
|---|---|
| `.stack` | show the whole stack |
| `.clear` | empty the stack |
| `.lcd [ascii]` | show the calculator screen as braille (or `ascii`) |
| `.key KEY...` | tap key(s) by name or hex matrix code, then show the screen |
| `.save [DIR]` | save state to `DIR`, else the `--state` dir |
| `.help` | list the commands |
| `.quit` | exit (bare `quit`/`exit` also work) |

`.key` names follow `docs/keymap.md`: digits `0`–`9`, letters `a`–`z` (HP 48
alpha layout — combine with `alpha` to type letters), and named keys/aliases
(`enter`, `del`, `back`, `lshift`, `rshift`, `alpha`, `on`, arrows
`up`/`down`/`left`/`right`, `+ - * /`, `sto`, `eval`, `sin`/`cos`/`tan`, …); an
unknown token falls back to a hex code. E.g. `.key 1 2 enter` (→ `12`),
`.key rshift sin` (ASIN). `.save` works whenever `--state` was given (with or
without `--save`), and `.save DIR` snapshots to any directory.

## See also

- `../examples/hp48.py` — full Python `ctypes` binding (display + stack).
- `../examples/rpl_eval.py` — use the emulator as a User RPL scripting engine
  (feed source text, get Python values back) + a REPL.  `calc48.py` (above) is
  the command-line front end built on the same `Rpl` class.
- `../examples/ffi_smoke.c` — minimal headless embedding (links only libcalc48).
- `../docs/keymap.md` — key-matrix codes and PC-keyboard mapping.
- `../docs/stack_io_plan.md` — RPL stack read/write design and internals.
- `include/hp48_ui.h`, `hp48_io.h`, `hp48_serial.h`, `hp48_rpl.h` — the callback
  and feature interfaces, each documented inline.
