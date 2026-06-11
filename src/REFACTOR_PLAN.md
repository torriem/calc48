# Plan: Separate the HP 48 Emulator Core from the SDL GUI

## Goal

Turn the current monolithic, global-state, C++-linked program into:

- A **self-contained, reentrant emulator core** (`libhp48`) with no UI/SDL
  dependency and no C++ dependency.
- An **instance struct** (`hp48_t`) that holds all emulator state, so the
  emulator can be instantiated and driven from C, Python, or other languages
  via a flat FFI-friendly C API.
- The existing SDL UI reduced to *one client* of that API.

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

### Phase 4 — Public C API (FFI surface)

Define a clean, flat `hp48.h` for embedders / Python:

- `hp48_create()` / `hp48_destroy()`
- `hp48_load_rom()`
- `hp48_load_state()` / `hp48_save_state()`
- `hp48_step()` / `hp48_run(n)`
- `hp48_press_key()` / `hp48_release_key()`
- `hp48_get_lcd()`
- `hp48_set_iface()`

### Phase 5 — Ship `libhp48.so` + Python binding

- Build `libhp48.a` / `libhp48.so` (pure C).
- Provide a Python binding example (cffi / ctypes).
- The SDL frontend becomes just one client of the same API.

## Notes

- Phases 0 and 1 are safe and independently committable; do them first.
- Phase 2 is the largest change but stays mechanical and testable thanks to the
  `#define` bridge.
