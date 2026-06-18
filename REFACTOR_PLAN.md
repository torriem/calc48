# Plan: Separate the HP 48 Emulator Core from the SDL GUI

## Goal

Turn the current monolithic, global-state, C++-linked program into:

- A **self-contained, reentrant emulator core** (`libhp48`) with no UI/SDL
  dependency and no C++ dependency.
- An **instance struct** (`hp48_t`) that holds all emulator state, so the
  emulator can be instantiated and driven from C, Python, or other languages
  via a flat FFI-friendly C API.
- The existing SDL UI reduced to *one client* of that API.

## Frontend strategy (decided)

**Split first, port the frontend later.** The core/GUI split (Phases 1-3)
reduces the UI coupling to three callbacks and makes the SDL frontend a small,
self-contained client. Only after that do we touch the frontend itself:

- The existing **SDL 1.2** frontend stays as the working reference during the
  split. It builds and runs on modern systems as-is, and `sdl12-compat` (SDL
  1.2 API over SDL2/SDL3) can keep it running with zero code changes if needed.
- Do **not** port the GUI to SDL3 before the split: the current frontend uses
  the SDL 1.2 software-surface model (`SDL_Surface` blitting + bundled
  `SDL_gfxPrimitives` / `SDL_rotozoom`), which SDL3 replaced with
  `SDL_Renderer`/`SDL_Texture`. An SDL3 port is effectively a frontend rewrite,
  so it must not be spent on code the split is about to gut.
- After the split, new frontends (a fresh **SDL3** frontend and/or a **Qt**
  frontend) are independent targets written against the callback interface,
  each linking the same `libhp48`. If porting to SDL, go straight to SDL3 and
  write fresh against the callbacks rather than porting the 1.2 surface code.

## Current architecture (findings)

### The GUI boundary is already small

SDL lives almost entirely in `x48_sdl.c` (plus the `SDL_gfx*` / `SDL_rotozoom*`
helper libraries). The emulator core touches SDL in only three places:

| Direction | Symbol | Location | Purpose |
|---|---|---|---|
| core → GUI | `SDLDrawNibble(x,y,val)` | `lcd.c:171` | draw one LCD nibble |
| core → GUI | `SDLDrawAnnunc(s)` | `lcd.c:352` | draw annunciators |
| core → GUI | `SDLGetEvent()` | `emulate.c:2431`, `actions.c:657` | pump input, returns keep-running |
| GUI → core | `saturn.keybuf[...]` + `do_kbd_int()` | `x48_sdl.c:811+` | key press / release |
| GUI → core | `init_emulator`, `init_active_stuff`, `emulate`, `emulate_debug`, `debug` | `mainsdl.c` | lifecycle / run loop |

### Global state to encapsulate

Everything below currently lives as file-scope globals and must move into the
instance struct:

- `saturn_t saturn` — *already a struct*; CPU registers, RAM/ROM/port pointers,
  all peripheral registers (`mainsdl.c:97`).
- `display_t display` + `disp_buf[][]` + `lcd_buffer[][]` (`lcd.c:87-91`).
- Scheduler state: ~25 longs/words in `emulate.c:80-122` (`instructions`,
  `sched_*`, `t1_i_per_tick`, `run`, `jumpaddr`, ...).
- Memory access function pointers `read_nibble` / `write_nibble` /
  `read_nibble_crc` and config (`init.c:99-106`: `ram_size`, `port1_*`,
  `port2_*`, `rom_is_new`).
- Timer offsets (`timer.c:89-100`).
- Debugger: `enter_debugger`, `in_debugger`, `exec_flags` + breakpoint tables
  (`debugger.c`).
- `got_alarm`, `conf_bank1/2` (`actions.c`).
- Config / options: `throttle`, `verbose`, `romFileName`, `homeDirectory`, ...
  (`resources.c:67-79`).

### The C++ dependency is incidental

The project builds with `g++` only by habit. As verified by test-compiling,
building as pure C needs exactly three trivial fixes:

1. `inline` helpers in `emulate.c` (e.g. `decode_8_thru_f`) must be
   `static inline` — same root cause as the `step_instruction()` linker bug
   already fixed.
2. Add `-lm` (SDL_rotozoom / gfx use `sincos`, `ceil`).
3. Add `-D_GNU_SOURCE` for `serial.c` (`ptsname_r`, `grantpt`, `unlockpt`).

## Design decision: reentrancy mechanism

**Chosen: global active-context pointer** (over passing the context to every
function).

- Aggregate all globals into a single `hp48_t` struct.
- Keep one `static hp48_t *cpu;` in the core, bound at the entry of each public
  API call.
- Bridge existing code with `#define saturn (cpu->saturn)` etc., so the many
  `saturn.PC`-style references compile unchanged.
- Supports multiple instances (one active at a time per thread). Small,
  low-risk, mechanical churn.
- Full multi-thread concurrency (TLS or full parameter threading) can be a
  later step if ever needed.

## Phased plan

### Phase 0 — Pure-C build *(prerequisite; ~15 min; zero behavior change)*

- Set `CC = gcc` in the Makefile.
- Mark `emulate.c` inline helpers `static inline`.
- Add `-lm` and `-D_GNU_SOURCE` to `CFLAGS`.
- Verify the binary still builds and runs.

Safe and independently committable.

### Phase 1 — Split Makefile into `libhp48` core + SDL frontend

- Define a core object set that links with **no** `-lSDL` and no
  `-I.../SDL`.
- The SDL frontend links against the core.
- The core link step becomes the enforcement mechanism for the boundary: any
  stray SDL symbol in the core fails the link.

Safe and independently committable.

### Phase 2 — `hp48_t` context struct + active pointer *(bulk of the work)*

- New `hp48_context.h` defining:

  ```c
  typedef struct hp48_t {
      saturn_t   saturn;
      display_t  display;
      /* scheduler state, timer offsets, memory config + fn pointers,
         debugger state, options, display buffers ... */
  } hp48_t;
  ```

- One `static hp48_t *cpu;` in the core, set at each public API entry.
- Bridge with `#define saturn (cpu->saturn)`, `#define display (cpu->display)`,
  etc., so existing references compile unchanged.
- Move the scattered globals (`emulate.c`, `init.c`, `timer.c`, `debugger.c`,
  `actions.c`, `resources.c`) into the struct, deleting their standalone
  definitions.
- Optionally remove the `#define` bridges incrementally, file-by-file, later.

Mechanical and testable at each step.

### Phase 3 — Callback table replaces the 3 SDL calls

- Add to `hp48_t`:

  ```c
  struct hp48_iface {
      void (*draw_nibble)(void *user, int x, int y, int val);
      void (*draw_annunc)(void *user, const char *annunc);
      int  (*poll_event)(void *user);
      void *user;
  };
  ```

- `lcd.c:171/352` and `emulate.c:2431` / `actions.c:657` call through
  `cpu->iface.*` instead of `SDLDrawNibble` / `SDLDrawAnnunc` / `SDLGetEvent`.
- Core now has zero SDL symbols → `libhp48` links standalone.

### Phase 4 — Public C API + cooperative event-loop model (FFI surface)

#### Control-model decision: cooperative `run_slice`, host owns the loop

Today the emulator **owns the thread and the loop**: `emulate()` is an infinite
`do { step_instruction(); … if (schedule_event-- <= 0) schedule(); } while
(!enter_debugger)`, paced by a process-global `SIGALRM`/`setitimer` (20 ms)
whose only job is to set `got_alarm`, which makes `schedule()` reach out and
pump the SDL event queue via `cpu->ui.get_event()`. That collides with any
toolkit (Qt) that owns its own loop, and a library must not install a global
`SIGALRM`.

Decision: invert control. The library exposes a bounded **`hp48_run_slice()`**
that runs a chunk and returns; the *host* owns the loop and calls it on a
timer. This is single-threaded, lock-free and identical across SDL, Qt, Python
and tests. An embedder who wants threading can run `while(running)
run_slice();` on their own thread on top of this — so the core stays
loop-agnostic and toolkit-agnostic.

Key facts that make this cheap (verified in the code):
- `SIGALRM` does **not** drive the CPU; it only paces UI polling. The CPU
  free-runs. Removing the signal costs nothing in execution.
- `schedule()` fires on an *instruction* countdown, and reconciles the HP48
  hardware timers (8192 Hz / 16 Hz) and `i_per_s` against `gettimeofday()`.
  That stays untouched, so the calculator's clock remains correct regardless
  of how often `run_slice()` is called.
- Pump cadence is the *display/input* rate (~50–60 Hz, 16–20 ms), decoupled
  from the CPU and timer rates; the in-slice scheduler advances the fine
  timers in bulk. `run_slice` should be wall-clock-budgeted (advance emulated
  time to catch up to real time, capped to avoid spirals) so it self-throttles
  and idles cheaply.

#### `emulate()` → `run_slice()` transformation

1. **One-time setup leaves the loop** into `hp48_start()` (timer resets, tick
   seeding, `set_t1`), called once after load.
2. **`run_slice(budget_us)`** is the old loop body with the exit condition
   inverted: loop `step_instruction()` + `schedule()` until a real-time budget
   elapses (checked every ~1024 instructions to avoid syscall overhead), or the
   calc halts, or `enter_debugger`. Returns a status (`RUNNING` / `HALTED` /
   `DEBUG`). `emulate_debug()` folds in as the same function with the
   breakpoint check.
3. **`SHUTDN` stops blocking (the crucial change).** `do_shutdown()` currently
   blocks in `do { pause(); … } while (!wake)` *inside* `step_instruction()`,
   sleeping on `SIGALRM` and pumping events itself — impossible in a
   cooperative slice. Rework it to do the pre-sleep bookkeeping, set
   `cpu->halted = 1`, and **return**. The wake-decision logic moves verbatim
   into `hp48_check_wakeup()` (called once per slice while halted, minus
   `pause()`/`got_alarm`). A sleeping calculator then costs ~one
   `gettimeofday` + a few checks per tick — effectively idle, no spin. New
   state: `int halted;` in `hp48_t`. This is the riskiest spot — verify
   against auto-off and the TIMER1/TIMER2 wake paths.

#### What is removed

- `setitimer` / `SIGALRM` / the `SIGALRM` arm of `signal_handler` / `got_alarm`.
- `pause()` in `do_shutdown`.
- **`cpu->ui.get_event` entirely**: in the cooperative model the host pumps its
  own events between slices and delivers keys via `hp48_press_key()`; the core
  never reaches out. Remaining `ui` callbacks (`draw_nibble`, `draw_annunc`,
  `adjust_contrast`, `show_connections`, `exit`) stay. ~20 ms input latency is
  imperceptible.

#### Host loops after the change

SDL front end (replaces the `setitimer` + `do { emulate(); } while(1)`):

```c
hp48_start();
for (;;) {
    int st = hp48_run_slice(20000);   /* ~20 ms of real time, or until halt */
    SDLGetEvent();                    /* host pumps its own events + keys */
    if (st == HP48_DEBUG) debug();
    SDL_Delay(frame_remaining_ms());  /* idle out the rest of the frame */
}
```

Qt — no thread, no signals; `QApplication::exec()` owns the loop:

```cpp
QTimer t; t.setInterval(20);
connect(&t, &QTimer::timeout, [&]{
    if (hp48_run_slice(20000)) lcdWidget->update();   /* repaint if dirty */
});
t.start();
```

#### Public C API (flat `hp48.h` for embedders / Python)

- `hp48_create()` / `hp48_destroy()` — allocate/free an `hp48_t`, rebind `cpu`.
- `hp48_load_rom()` / `hp48_load_state()` / `hp48_save_state()`.
- `hp48_set_ui()` (already exists) — install the rendering callbacks.
- `hp48_start()` — one-time run setup.
- `hp48_run_slice(budget_us)` — cooperative slice; returns RUNNING/HALTED/DEBUG.
- `hp48_step()` — single instruction (debug/test/Python REPL use).
- `hp48_press_key()` / `hp48_release_key()` — input, delivered between slices.
- `hp48_get_lcd()` — read the LCD pixel buffer (for hosts that pull rather than
  receive `draw_nibble` pushes).

### Phase 5 — Persistence API, `libhp48.so`, and a Python binding

#### How persistence works today (to be wrapped, not rewritten)

The emulator manages four on-disk resources, all in one directory
(`homeDirectory`, default `~/.hp48`, resolved by `get_home_directory`,
init.c:1106). That directory is both the load source and the save target.

| File          | Contents                                            | Saved?            |
|---------------|-----------------------------------------------------|-------------------|
| `rom`         | HP48 ROM image (GX/SX detected from it -> `opt_gx`)  | no (read-only)    |
| `hp48`        | the whole `saturn_t` CPU + peripheral snapshot       | yes               |
| `ram`         | calculator RAM, packed two nibbles/byte              | yes               |
| `port1`,`port2`| plug-in memory cards (RAM/ROM), packed nibbles      | yes, if present   |

- **Load** -- `init_emulator()` (init.c:1790): if `!initialize` and
  `read_files()` (init.c:1157) succeeds, the saved set is loaded; otherwise
  `init_saturn()` + `read_rom(romFileName)` boots fresh from ROM only.
  `read_files()` reads `rom`, then `hp48` (a MAGIC + 4-byte version header with
  versioned readers `read_version_0_3_0/0_4_0_file`, and an old raw-struct
  fallback via `old_saturn`), mallocs and reads `ram`, and optionally
  `stat`s/loads `port1`/`port2` (size from the file; RAM-vs-ROM inferred from
  the file's `S_IWUSR` write bit -> `card_status`).
- **Save** -- `exit_emulator()` -> `write_files()` (init.c:1605): mkdir the dir
  (fallback `/tmp`), write `hp48` field-by-field (`write_8/16/32/char`, so the
  format is endian-independent and versioned), then `ram`/`port1`/`port2`. The
  ROM is never written. **Save is exit-only; there is no autosave.**

Already instance-aware: these operate on `cpu->saturn` and the migrated
`opt_gx`/`rom_size`/`port*`/`rom_is_new`, so they act on the active instance
via the Phase 2 bridge. The rough edges are the *paths* and the *conflation*
of load-vs-fresh, both addressed below.

#### Persistence API (wrap the existing readers/writers; explicit paths)

- `hp48_load_rom(const char *path)` -- thin wrapper over `read_rom_file`;
  populates `cpu->saturn.rom`, `rom_size`, `opt_gx`.
- `hp48_load_state(const char *dir)` -- `read_files()` against an explicit
  directory instead of the global `homeDirectory` (keeps the MAGIC/version and
  legacy-format handling).
- `hp48_save_state(const char *dir)` -- `write_files()` to an explicit
  directory; callable by the host whenever it wants (not just at exit).
- `hp48_init_from_rom()` -- the `init_saturn()` + ROM path, split out from
  `init_emulator()` so "load existing" and "fresh boot" are distinct API calls
  rather than one flag (`initialize`).

Implementation note: refactor `read_files`/`write_files`/`get_home_directory`
to take a directory argument; the current SDL front end passes
`homeDirectory`, so its behavior is unchanged. This also removes the core's
hidden dependency on `get_resources()` for paths -- a step toward folding the
remaining `resources.c` config into the instance / an `hp48_config` (the
Phase 2 deferred item).

#### Ship it

- Build `libhp48.a` / `libhp48.so` (pure C, no SDL -- already true as of
  Phase 3).
- Provide a Python binding example (cffi / ctypes) exercising create -> load
  rom/state -> set_ui (or get_lcd) -> run_slice -> press_key -> save_state.
- The SDL front end becomes just one client of the same API.

### Phase 6 — Portable serial interface (host-provided transport)

Mirror the storage refactor (`hp48_io_t`) for the UART. Today the core's serial
emulation is split between *register behavior* (portable) and *transport* (not):

- **Register behavior** — `RCS`/`TCS`/`RBR`/`TBR`, status bits, and
  interrupt raising — is memory-mapped IO in `memory.c` plus the logic in
  `serial.c` (`transmit_char`/`receive_char`), driven from `device.c`,
  `emulate.c`, `actions.c`. This is clean, instance-aware, and stays in the core.
- **Transport** — the actual "wire" — is the non-portable part: `serial_init()`
  opens a host **pseudo-terminal** (`/dev/ptmx`, `grantpt`/`unlockpt`/`ptsname_r`,
  with BSD/IRIX/Solaris variants) and `transmit_char`/`receive_char` do raw
  `open()`/`read()`/`write()`/`select()` on `wire_fd`/`ir_fd`. It also still
  references frontend globals (`progname`, `verbose`, `useTerminal`). This is the
  last host-syscall dependency in the core and won't build on Android / a pure-Qt
  target.

#### Callback interface

Add `hp48_serial_t` (new `libcalc48/include/hp48_serial.h`), stored on `hp48_t`,
installed via `hp48_set_serial()` — modeled exactly on `hp48_io_t`/`hp48_ui_t`,
NULL-guarded so a headless host with no serial simply has the wire "unplugged"
(the UART reports no device, as `wire_fd == -1` does now). Byte-stream oriented,
since that is all `transmit_char`/`receive_char` need:

```c
typedef struct hp48_serial_t {
  /* channel: 0 = wire, 1 = IR (selected by saturn.ir_ctrl & 0x04) */
  int  (*open)(void *user, int channel);            /* >=0 ok, <0 unplugged */
  void (*configure)(void *user, int channel, int baud);
  int  (*read)(void *user, int channel,             /* NON-blocking; returns  */
               unsigned char *buf, int max);        /* n>=0, 0 = none ready   */
  int  (*write)(void *user, int channel,            /* returns n written, or  */
                const unsigned char *buf, int n);   /* <0 = error/would-block */
  void (*close)(void *user, int channel);
  void *user;
} hp48_serial_t;
```

Only the transport moves behind this boundary; the register emulation and
interrupt logic in the core are unchanged. `serial.c` stops calling
`open`/`read`/`write`/`select` directly and instead calls `cpu->serial.*`,
keeping its non-blocking contract (the core polls from `device.c`/`emulate.c`, so
`read` must return 0 immediately when nothing is ready — never block). The two
channels (wire / IR, chosen by `ir_ctrl & 0x04`) become the `channel` argument.
`baud` is delivered via `configure` (cosmetic for most hosts, real for a PTY).

#### Default (bundled) providers

Like `hp48_io_stdio`, ship optional providers behind a CMake option
(`HP48_WITH_SERIAL`), so the bare core stays syscall-free:

- **Host serial / PTY provider** (`hp48_serial_pty.c`) — the current `/dev/ptmx`
  behavior, lifted out of the core into a provider and **de-globalized** (no
  `progname`/`verbose`/`useTerminal`; name/verbosity passed in or reported back).
  Also covers connecting to a real host serial device (`/dev/ttyUSB0`, …) by
  path. This is where today's PTY-naming UI belongs — the frontend already owns
  the connection display (`show_connections`/`update_connection_display`).
- **Socket provider** (`hp48_serial_socket.c`) — TCP (listen/connect) and/or
  Unix-domain sockets. Same callback surface, `select()` on a socket fd instead
  of a PTY. Enables wiring two emulator instances together, or connecting a host
  tool (Kermit, a test harness) over the network — handy on platforms with no
  PTY.

#### Frontend-provided transport

A host that wants a virtual mechanism implements `hp48_serial_t` directly and the
core never touches an fd: an in-memory pipe between two `hp48_t` instances, a Qt
`QIODevice`/`QSerialPort`, an Android USB/Bluetooth stream, or a deterministic
test fixture feeding canned bytes. The SDL front end becomes one such client,
installing the PTY provider by default (preserving today's behavior).

#### Migration / notes

- Extract transport from `serial.c` into the callback boundary; keep all
  register/interrupt logic in the core. Replace `serial_init()`'s PTY open with
  provider install; the frontend (SDL) installs the PTY provider, matching the
  storage split where the frontend chose the `stdio` provider.
- Persistence is unaffected: `rcs`/`tcs`/`rbr`/`tbr` are already saved
  (`init.c`); the transport (fds/sockets) is *not* persisted — the host
  re-establishes the connection on load, exactly as today.
- Keep IR vs wire as distinct channels; a provider may support only the wire and
  return "unplugged" for IR.

### Phase 7 — Remaining host couplings: clock seam, build options, cleanup

After Phase 6 the only host dependencies left in the core are the wall clock and
the interactive debugger, plus some dead code. This phase removes them so the
bare core is pure-C with no host syscalls.

#### Clock seam (the last always-on host dependency)

`timer.c` (~8 sites) and `emulate.c:2423/2444` call `gettimeofday()` directly to
drive the calculator's real-time clock and pace execution. It is POSIX, so it
already works on Linux / macOS / **Android (NDK)** — it only fails on **Windows**
— but it is also a *policy* coupling: an embedder can't feed a virtual or
deterministic clock (for tests, or to decouple calc time from real time).

Add a one-function time seam, overridable like the other callbacks:

```c
/* monotonic-ish microseconds; default impl wraps gettimeofday() */
uint64_t (*now_us)(void *user);   /* on hp48_t, or a settable global hook */
```

- Default provider uses `gettimeofday()` (a Windows impl can use
  `QueryPerformanceCounter`/`GetSystemTimeAsFileTime` without touching the core).
- Route all `timer.c`/`emulate.c` time reads through it; keep the existing tick
  math. The calculator clock then tracks whatever the host returns, enabling
  deterministic tests and virtual time.
- Drop the SOLARIS/SUNOS `extern int gettimeofday` re-declarations
  (`timer.c:68/71`) — K&R leftovers.

#### Core build options (compile out non-embeddable parts)

Mirror `HP48_WITH_STDIO_IO`, so the embeddable core carries nothing it doesn't
need:

- **`HP48_WITH_DEBUGGER`** (default ON) — the interactive debugger (`debugger.c`)
  reads commands from stdin (`read()` on fd 0 via `read_str`, `debugger.c:352`,
  with `HAVE_READLINE`/`SUNOS`/`SIMPLE_64` ifdefs). It is terminal-bound and a
  singleton dev tool an Android/Qt embed never wants. Gate it so it can be
  compiled out entirely; the breakpoint hook (`hp48_debug_check`) becomes a
  no-op when absent.

#### Dead-code / leftover cleanup

- **Audio beeper** (`device.c:181–204`): `check_out_register()` opens
  `/dev/audio` and `write()`s — but the whole block is `#if 0`, so it is not
  compiled. Delete it. Note this also means **sound is not emulated** (the
  `OUT[2]` beeper bit is dropped); if wanted later it belongs as a `hp48_ui_t`-
  style callback, never a `/dev/audio` open.
- **Stale includes** (`init.c`): still includes `<unistd.h>`, `<sys/stat.h>`,
  `<pwd.h>` though `getpwuid`/`getenv`/`stat`/`mkdir` moved to the frontend /
  stdio provider when path policy was split out. Remove them (`pwd.h` is itself
  non-portable).

#### Outcome

With the clock seam in place and the debugger gated off, the core
(`HP48_WITH_STDIO_IO=OFF`, `HP48_WITH_SERIAL=OFF`, `HP48_WITH_DEBUGGER=OFF`) has
**no host syscalls at all** — all host interaction (storage, serial, time,
rendering) flows through the callback tables, which is the end state this refactor
has been driving toward.

## Progress / status

- **Phase 0 — done.** Pure-C CMake build (no g++), executable `x48`.
- **Phase 1 — done.** `libhp48` static library + SDL frontend; the only
  frontend symbols the core references are the three Phase 3 callbacks.
- **Phase 2 — substantially done.** The mechanism (`hp48_t`, the active-context
  pointer `cpu`, and the `hp48_state.h` bridge macros) is in place, and all
  genuine *runtime emulator state* now lives in `hp48_t`:
  - saturn CPU/peripheral registers; LCD display + buffers; the instruction
    loop / event scheduler; memory accessors (`read/write_nibble`) and the
    port configuration; wall-clock timer offsets; interrupt/keyboard/bank
    state; ROM identity (`opt_gx`, `rom_size`); the `device_t` I/O block; and
    the serial/IR file descriptors.
  - Bridging notes: most globals are reached via `#define NAME (cpu->NAME)`.
    Names that collide with SDL or with a local/member are handled directly:
    `display` (collides with an SDL_video.h param) and `run` (collides with a
    timer.c local/member) use `cpu->...` at their call sites instead of a
    macro. `context.c` defines `HP48_STATE_NO_BRIDGE` so it can initialize the
    struct by real field names. Non-zero defaults that were static initializers
    are seeded in `context.c`'s instance initializer. The dead global
    `ram_size` was dropped.

  Deliberately **left as globals** for now (not per-instance runtime state):
  - **Interactive debugger** (`enter_debugger`, `in_debugger`, `exec_flags`,
    `num_bkpts`, `bkpt_tbl`, command buffers): a singleton development tool; its
    breakpoint type is debugger-internal. Revisit only if per-instance
    debugging is needed.
  - **Startup configuration** (`resources.c`: `throttle`, `romFileName`,
    `homeDirectory`, `verbose`, …): populated from argv / X-resources. This is
    config, not state, and is better threaded through `hp48_create()` in
    Phase 4 than swept into the struct now.
  - **Read-only lookup tables** (`nibble_masks`, `conf_tab_*`, disasm tables,
    `unix_0_time`, `ticks_10_min`, …): correctly shared; not per-instance.
  - **Scratch buffers** (`errbuf`, `fixbuf`, and the `old_saturn` /
    `saturn_0_3_0` state-file conversion temporaries).

  Remaining for a future Phase 2 follow-up if full multi-instance config is
  wanted: fold the resources config into `hp48_t` (or an `hp48_config` passed
  to create), and decide on debugger ownership.
- **Phase 3 — done.** The core no longer depends on SDL. A new SDL-free header
  `hp48_ui.h` defines `hp48_ui_t` (callbacks: draw_nibble, draw_annunc,
  get_event, adjust_contrast, show_connections, exit) and the `disp_t` LCD
  geometry; both live on `hp48_t`, installed via `hp48_set_ui()`. The six
  former direct SDL calls now go through `cpu->ui.*` (NULL-guarded for headless
  use). `ann_tbl` (SDL surfaces) moved to the front end, which now receives the
  raw annunciator bitmask; `disp` moved into `hp48_t`; `progname` is core-owned.
  The SDL front end registers its callbacks in `register_sdl_ui()`. Verified:
  `libhp48.a` has zero `SDL_*` references and zero external non-libc symbols,
  and the `hp48` CMake target builds with no SDL headers, no SDL link and no
  libm -- so it can be built as a standalone shared library.
- **Phase 4 — done.** Cooperative control model: `emulate()` ->
  `hp48_start()` + `hp48_run_slice(budget_us)` (returns RUNNING/HALTED/DEBUG);
  `SHUTDN` reworked to the non-blocking `halted` state + `hp48_check_wakeup()`;
  debugger breakpoint check moved to `hp48_debug_check()` and `emulate_debug()`
  removed; `cpu->ui.get_event` dropped; the process-global `SIGALRM`/`setitimer`
  removed; `mainsdl.c` is now a cooperative loop. FFI surface added:
  `hp48_create()`/`destroy()`/`set_active()`/`init_defaults()`, `hp48_step()`,
  `hp48_press_key()`/`release_key()`, `hp48_get_lcd()` (the SDL button handlers
  now call the key API). `examples/ffi_smoke.c` (CMake target `hp48_ffi_smoke`)
  links ONLY libhp48 -- a per-build check that the core is self-contained.
  Core verified SDL-free; boots and idles (~4% CPU asleep); the example drives
  create/get_lcd/press/release/destroy with no SDL.
  Still wanted: interactive verification of the SDL app (render + key response)
  on a display.
- **Phase 5 — done.** Persistence API with explicit paths (`hp48_load_rom`,
  `hp48_init_from_rom`, `hp48_load_state`, `hp48_save_state`) by giving
  `read_files`/`write_files`/`get_home_directory` a directory argument (SDL app
  unchanged, passes `homeDirectory`) and fixing `read_rom()` to use its `fname`
  argument. Added `hp48_display_init()` so pull-based headless hosts get a live
  LCD (update_display gates on `disp.mapped`, which only the SDL window set).
  `libhp48` now builds both STATIC (`libhp48.a`) and SHARED (`libhp48.so`,
  versioned, PIC, depends only on libc). `examples/hp48.py` (ctypes) drives the
  whole API and renders the LCD as terminal ASCII -- verified against a real
  `~/.hp48` state, showing the live stack display with no SDL.
  Note: the Python example is headless (terminal output), not a GUI; a windowed
  demo (pygame / Qt) would be a follow-up.
- **Phase 6 — done.** Portable serial transport via the `hp48_serial_t` callback
  interface (`hp48_serial.h`, installed with `hp48_set_serial`). `serial.c` now
  emulates only the UART (RCS/TCS/RBR/TBR + interrupts) and is syscall-free
  (verified: no `open`/`read`/`write`/`select`/socket externals); the wire/IR
  transport goes through `cpu->serial.*` with `wire_fd`/`ir_fd` reduced to
  per-channel connected flags (`ttyp` removed). Two bundled providers behind the
  `HP48_WITH_SERIAL` option: `hp48_serial_pty.c` (the de-globalized PTY/device
  code, installed by the SDL front end from its resources) and
  `hp48_serial_socket.c` (TCP listen/connect + Unix-domain). With
  `HP48_WITH_STDIO_IO=OFF` and `HP48_WITH_SERIAL=OFF` the core links with no
  file/serial host syscalls. Verified: full build (core static+shared, SDL app,
  FFI smoke) clean; the socket provider round-trips RX/TX over TCP loopback
  through the real `receive_char`/`transmit_char`; `examples/hp48.py` still boots
  `~/.hp48` and renders the LCD.
- **Phase 7 — planned (not started).** Remove the remaining host couplings: a
  `now_us` clock seam over `gettimeofday()` (the last always-on dependency;
  enables Windows + virtual/deterministic time), a `HP48_WITH_DEBUGGER` build
  option to compile out the stdin-bound debugger, and dead-code cleanup (the
  `#if 0` `/dev/audio` beeper in `device.c`, stale `pwd.h`/`unistd.h`/`sys/stat.h`
  includes in `init.c`). End state: core with all `HP48_WITH_*` off has no host
  syscalls.

## Notes

- Phases 0 and 1 are safe and independently committable; do them first.
- Phase 2 is the largest change but stays mechanical and testable thanks to the
  `#define` bridge.
- The single static instance in `context.c` keeps current behavior; the active
  pointer `cpu` is rebindable, which is what Phase 4's create/destroy will use.
