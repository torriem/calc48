# Plan: read (and later write) RPL stack objects via the API

Goal: expose API calls on `libcalc48` to **read** objects off the HP 48 RPL
user stack, and later to **write/push** objects — an injection path that
doesn't go through ports.

This document plans the READ side concretely and sketches WRITE for later.

## Background (where the stack lives)

The RPL user/data stack is reached through two system-RAM pointers, whose
addresses the emulator already hard-codes (`libcalc48/src/debugger.c`):

| | DSKTOP | DSKBOT |
|---|---|---|
| SX | `0x70579` | `0x7057e` |
| GX | `0x806f8` | `0x806fd` |

Layout (already walked by `do_stack`/`do_rpl` in debugger.c):

- `[DSKTOP]` (5 nibbles) -> `sp`, the address of the stack pointer array.
- `[DSKBOT]` (5 nibbles) -> `end`, just past the bottom.
- The array holds one **5-nibble object pointer per level**: level 1 at `sp`,
  level 2 at `sp+5`, ... `depth = (end - sp)/5 - 1`.
- Each object starts with a 5-nibble **prologue** (its type), followed by a
  type-dependent body.

Existing building blocks (all instance-aware via the `cpu` bridge):
- `read_nibbles(addr, len)` / `write_nibbles(addr, val, len)` — `memory.c`
  (honor the live memory mapping through `read_nibble`/`write_nibble`).
- `load_addr(&dst, addr, 5)` — `actions.c` (read a 20-bit pointer).
- `decode_rpl_obj(addr, buf)` / `decode_rpl_obj_2(addr, typ, dat)` — `rpl.c`
  (decode an object to text; knows how to traverse the common types).

`docs/saturn.txt` is a CPU reference and does **not** cover this (its "stack"
is the CPU return stack, RSTK).

## Phase R — READ (do first)

### R0. New core module
Add `libcalc48/src/rplstack.c` + a public header (e.g. `hp48_rpl.h` in
`libcalc48/include`, or fold prototypes into `hp48.h`). Wire into
`libcalc48/CMakeLists.txt` `CORE_SOURCES`. Operates on the active instance.

Move/share the `DSKTOP_*`/`DSKBOT_*` constants (currently private to
debugger.c) into the new module's header so both use one definition.

### R1. Stack walk (refactor of do_stack, no debugger I/O)
Internal helper that resolves `sp`, `end`, `depth` for the active instance,
choosing the SX vs GX pointer addresses from `opt_gx`.
- Use the live `read_nibble` path (do **not** force `mem_cntl[1]` the way
  do_stack does; that is only needed for a "cold" debugger read — a running
  instance already has RAM configured).
- Treat this as read-only and side-effect free.

### R2. Object length / "skip object"  *(the hard part of READ)*
To copy a whole object out, we need its total nibble length. Object size is
type-dependent (prologue + body, with length encodings or a terminating SEMI
for composites). Options:
1. Extend the traversal logic already in `decode_rpl_obj` (rpl.c) to also
   return a length — preferred, reuses working code.
2. Implement a standalone `rpl_object_size(addr)` covering the standard
   prologues (DOINT, DOREAL, DOCSTR/string, DOLIST/composite, DOCOL/program,
   DOIDNT, DOARRY, ...).
- First cut may support the common types and return "unknown" for the rest;
  callers can still get the address, prologue, and decoded text.

### R3. Public API surface
```c
int      hp48_stack_depth(void);                       /* 0 if empty       */
uint32_t hp48_stack_addr(int level);                   /* level 1 = top    */
uint32_t hp48_object_prolog(uint32_t addr);            /* 5-nibble type id */
int      hp48_object_size(uint32_t addr);              /* nibbles, <0 = ?  */
int      hp48_read_object(uint32_t addr,               /* raw nibble copy  */
                          unsigned char *out, int max); /* returns n, or
                                                          needed if > max  */
/* convenience: decoded text of a level, like the debugger's view */
int      hp48_stack_describe(int level, char *out, int max);
```
Nibble buffers are one nibble per byte (matching `lcd`/memory conventions);
document the packing.

### R4. Caveats to honor / document
- **Safe point:** read while the calculator is idle / between `run_slice`
  calls, not mid-instruction.
- **Mapping:** values are absolute addresses in the configured space; rely on
  the live mapping (`read_nibble`), so RAM must be configured (it is, once the
  calc has booted).
- **SX vs GX:** select pointer addresses from `opt_gx`.

### R5. Validation
- Cross-check `hp48_stack_depth`/`hp48_stack_describe` against the debugger's
  `do_stack` output for a known state.
- Exercise from Python (extend `examples/`): load `~/.hp48`, run briefly, print
  the decoded stack — a read-only, low-risk end-to-end test.

## Phase W — WRITE / push (later, separate effort)

Pushing is the hard direction, because the object **body** must live in RAM the
RPL memory manager owns: a correct push has to place the object, grow the stack
array, move `DSKTOP`, and keep the free / `TEMPOB` / `AVMEM` pointers
consistent. Two ways to do that, taken in this order.

### W1. Offline / whole-image edit  *(recommended)*

Edit the RAM while the emulator is **not running**, then boot it. This removes
the worst part of a live push — atomicity. With RAM frozen, every pointer
update lands before any instruction executes, so RPL never sees a half-built
state and the GC can't corrupt it. It is deterministic and needs no safe-point
hunting.

It does *not* remove the need for the memory map (the `TEMPOB`/free/`AVMEM`
addresses) or the object-size logic (R2) — offline makes a push *safe*, not
*free*.

The clean shape, given that RAM is already a host-supplied blob:
- **Host-side tool (preferred):** a separate program (e.g. Python) that reads
  the `ram` + `hp48` resources, edits the stack in the bytes, and writes them
  back — then the emulator boots them normally. No running emulator, no new C
  in the hot path; a pure data transform over the same files `hp48_io_t` moves.
- **In-process variant:** the same edit done in C in the window between
  `hp48_load_state()` and `hp48_start()`, where `saturn.ram` is loaded and
  quiescent.

Prefer **parse -> modify -> re-serialize the whole image** over surgical poking:
read the saved RAM into a model (stack levels, TEMPOB objects, free region),
append the object, and write RAM back with all affected pointers recomputed.
Owning the whole layout is far easier to get correct than not disturbing a live
one. ROM-version specific (key off `opt_gx`).

#### Validate before edit (precondition, not assumption)

W1 needs a *clean idle* snapshot, but **nothing in the OS stamps an "idle"
flag** — so don't assume it, check it. The save is taken at a CPU instruction
boundary (the host loop / `run_slice` cadence), so the image is never
mid-instruction torn; but it is **not** gated on RPL being idle (the host can
quit while a program runs). "Saved states are idle" is true in practice, not by
construction.

Turn it into a checkable precondition. Before editing, run a structural
validation pass over the image — it reuses the object-sizing logic R2/W1 already
need, and is cheap:
- Walk `DSKTOP → DSKBOT`: `depth = (end-sp)/5 - 1` is a sane non-negative
  integer; every 5-nibble slot points into a valid region.
- Walk the `TEMPOB` chain + free / `AVMEM` pointers: they partition memory
  without overlap, and each object's prologue + body length lands exactly on the
  next boundary (no length runs past `AVMEM`/end).

If those invariants hold, the image is **not torn** (no half-built object, no
dangling pointer) — which is exactly what makes an offline edit safe. Caveat:
they hold at *every* RPL instruction boundary, so they prove *consistent*, not
specifically *idle at the outer loop*.

For a belt-and-suspenders "was idle" heuristic, look at the saved **CPU state**
(the `hp48` state file, not the `ram` blob — it serializes `PC`, the hardware
`rstk[]`, etc.): a `PC` parked in the keyboard-wait / `SHUTDN` light-sleep code
(the emulator's `halted` / `HP48_HALTED` condition) is the cleanest signal the
calc was idle at the outer loop. The RPL return stack (RAM-resident, distinct
from the hardware `rstk[]`) depth above baseline is the real "a program is
running" tell, but reading it needs the same memory-map work as W2.

### W2. Live push  *(only if injecting into a running calc)*

Do the allocation + pointer fixups on a running instance at a safe point (calc
idle at the outer loop, between `run_slice` calls):
1. allocate space in `TEMPOB` (move the free pointer; respect GC),
2. write the object nibbles there,
3. decrement `DSKTOP` by 5 and store the new object's address in the new slot,
4. keep `TEMPTOP`/free, `AVMEM`, etc. consistent.
Higher risk than W1 because the updates must be atomic relative to execution.
Lower-risk live alternatives that reuse the calc's own allocator: inject as a
**global variable** (store into a directory) and recall it, or
**keyboard/command-line injection** (type text + ENTER via `hp48_press_key`).

### Shared prerequisites (both paths)
- Locate the SX/GX addresses of the memory-manager pointers (`TEMPOB`, free /
  `TEMPTOP`, `AVMEM`) — same system-RAM tables that give `DSKTOP`/`DSKBOT`.
- Build `rpl_object_size()` (R2) first; WRITE needs object lengths too.

## Notes
- All new core code is in `libcalc48`, instance-aware via the `cpu` bridge; no
  SDL dependency. The W1 host-side tool needs no core changes at all.
- READ is high-value / low-risk and unblocks inspection and serialization;
  WRITE is gated on mapping the memory manager and is best done as its own pass,
  with the offline (W1) approach preferred.
