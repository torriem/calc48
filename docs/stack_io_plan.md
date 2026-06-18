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

## Reference implementation (droid48 `binio.c`)

A sibling emulator (droid48, `app/src/main/jni/binio.c`) already implements
runtime stack read/write, and most of it ports onto libcalc48 directly because
**we already have the building blocks**:

| droid48 helper | libcalc48 equivalent (already present) |
|---|---|
| `Npeek(buf,addr,n)` (read nibbles) | `load_address(buf,addr,n)` — `actions.c`, uses `read_nibble` |
| `Nwrite(buf,addr,n)` (write nibbles) | `store_n(addr,buf,n)` — `actions.c`, uses `write_nibble` |
| `Read5(addr)` (read 20-bit ptr) | `load_addr(&v,addr,5)` — `actions.c` |
| `RPL_ObjectSize()` (object length) | **port this** — solves R2; all prologue constants already in `rpl.h` |
| prologue ids (`DOBINT`, `DOCSTR`, `SEMI`, …) | already in `libcalc48/include/rpl.h` (more complete than droid48) |
| `DSKTOP`/`DSKBOT` | `debugger.c` (`DSKTOP_SX/GX`, `DSKBOT_SX/GX`) |
| `TEMPOB`/`TEMPTOP`/`RSKTOP`/`AVMEM` | **GX only** (from droid48); SX `AVMEM` still unknown |

droid48's memory-manager pointers are **GX**: `TEMPOB=0x806E9`,
`TEMPTOP=0x806EE`, `RSKTOP=0x806F3`, `DSKTOP=0x806F8`, `AVMEM=0x807ED` (its
`DSKTOP` matches our `DSKTOP_GX`). The first four are a consecutive run of
5-nibble slots (`TEMPOB, TEMPTOP, RSKTOP, DSKTOP, DSKBOT`); `AVMEM` is not in
that run. So for the WRITE side, the GX manager addresses are known but the SX
`AVMEM` address still needs a real lookup — which is why the runtime stack I/O
is built **GX-only** for now (CMake `HP48_GX_ONLY`).

## Phase R — READ (do first)

**Status: implemented** (`libcalc48/src/rplstack.c`, `include/hp48_rpl.h`, behind
`HP48_GX_ONLY`). `hp48_stack_depth`/`_addr`/`_object_prolog`/`_object_size`/
`_read_object`/`_stack_describe` verified against a real `~/.hp48` state
(HELLO/1234/777) and demonstrated from `examples/hp48.py`. The R2 fix vs droid48:
bare `DOIDNT`/`DOLAM` names have no trailing object (size `7 + 2*chars`); only
`DOTAG` carries one.

### R0. New core module
Add `libcalc48/src/rplstack.c` + a public header `hp48_rpl.h` in
`libcalc48/include`. Wire into `libcalc48/CMakeLists.txt` `CORE_SOURCES`,
**gated behind the `HP48_GX_ONLY` CMake option** (default OFF): the module is
GX-only (see the reference-implementation note), so it is compiled only when the
build declares a GX target. Operates on the active instance via the `cpu`
bridge. The module defines the GX `DSKTOP`/`DSKBOT` constants locally (debugger.c
keeps its own copies; deduping them is optional cleanup).

### R1. Stack walk (refactor of do_stack)
Internal helper that resolves `sp`, `end`, `depth` from the **GX** pointer
addresses (`DSKTOP_GX`/`DSKBOT_GX`), guarding on `opt_gx` so a GX-only build that
is handed an SX ROM fails safe (depth 0) rather than reading garbage.
- `depth = (end - sp)/5 - 1`; the level-`i` object pointer is at `sp + 5*i`
  (level 1 = top), exactly as `do_stack` walks it.
- Mirror `do_stack`'s force/restore of `mem_cntl[1].config` to the known GX RAM
  mapping (`0x80000`/`0xc0000`) around the reads, then restore — proven, and
  side-effect free because it is restored.

### R2. Object length / "skip object"  — *solved by porting droid48*
Port droid48's `RPL_ObjectSize` as `rpl_object_size(word_20 addr)`, reading
prologues straight from memory via `read_nibble` (no pre-loaded buffer). It
switches on the prologue and recurses into composites (`DOLIST`/`DOCOL`/`DOSYMB`/
`DOEXT` to `SEMI`), tagged/named objects (`DOIDNT`/`DOLAM`/`DOTAG`) and
directories (`DORRP`); fixed-size types (reals, complex, char, …) and
length-prefixed types (`DOCSTR`/`DOARRY`/`DOHSTR`/`DOGROB`/`DOCODE`/…) are
table-direct. All the prologue constants it needs are already in `rpl.h`. Unknown
prologues fall back to the 5-nibble prologue length (caller still gets the
address + decoded text). Add a recursion-depth guard against corrupt data.

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

### W2. Live push  *(GX-only; the droid48 recipe)*

droid48's `binio.c` proves a live push is tractable and hands us the exact
mechanism (`RPL_CreateTemp` + `RPL_Push`). Port both into `rplstack.c` behind the
same `HP48_GX_ONLY` gate as the read API, then expose a push entry point.

#### Memory layout this relies on (GX)

The five manager pointers are consecutive 5-nibble slots; `AVMEM` is separate.
Their *values* partition user RAM in increasing address (verified by reading
them and by `RPL_CreateTemp`'s arithmetic):

```
... TEMPOB objects ...[TEMPTOP] return stack [RSKTOP] free gap [DSKTOP] data-stack ptr array [DSKBOT]
```

| pointer | GX addr | holds |
|---|---|---|
| `TEMPOB`  | `0x806E9` | start of the temporary-object area |
| `TEMPTOP` | `0x806EE` | end of the top temp object (= start of return stack) |
| `RSKTOP`  | `0x806F3` | end of the return stack (= start of the free gap) |
| `DSKTOP`  | `0x806F8` | top of the data stack (level-1 pointer slot) |
| `DSKBOT`  | `0x806FD` | just past the bottom of the data stack |
| `AVMEM`   | `0x807ED` | free memory in 5-nibble units |

**Key insight (why no pointer fixups are needed):** the new object is inserted
at the *top* of TEMPOB (at the old `[TEMPTOP]`), and only the **return stack** is
slid up to make room. No existing temp object moves, so every existing stack
pointer stays valid; and the return-stack entries are absolute addresses, so
relocating the block doesn't change their meaning. This is what makes the push
safe without walking and rewriting pointers.

#### W2.1 Constants + helpers
- Add `TEMPOB_GX`/`TEMPTOP_GX`/`RSKTOP_GX`/`AVMEM_GX` to `rpl.h` (GX values
  above; `DSKTOP_GX`/`DSKBOT_GX` already there).
- We already have `read_packed` (rplstack.c) and `store_n` (writes a nibble
  buffer). Add `write_packed(addr, value, n)` — the `Write5` equivalent (write
  `n` nibbles LSB-first via `write_nibble`).
- `rpl_object_size()` (R2) is done.

#### W2.2 Port `RPL_CreateTemp` as `rpl_alloc_temp(int nibbles) -> addr`
1. `total = nibbles + 6`  (1 marker nibble + 5-nibble length/link field).
2. `a = read5(TEMPTOP); b = read5(RSKTOP); c = read5(DSKTOP)`.
3. if `b + total > c` → **out of memory**, return 0 (no GC; see caveats).
4. `write5(TEMPTOP, a+total); write5(RSKTOP, b+total)`.
5. `write5(AVMEM, (c - b - total) / 5)`.
6. Relocate the return stack: copy nibbles `[a, b)` → `[a+total, b+total)`. The
   ranges overlap (moving up by `total`), so copy via a scratch buffer (as binio
   does) or copy high→low; do **not** use a naive low→high `store_n`.
7. `write5(a+total-5, total)`  (the object's trailing length field).
8. return `a+1`  (object base = where the prologue goes).

#### W2.3 Port `RPL_Push` as `rpl_push(addr)`
1. `avmem = read5(AVMEM)`; if `avmem == 0` → fail; `avmem--`; `write5(AVMEM, avmem)`.
2. `stkp = read5(DSKTOP); stkp -= 5; write5(stkp, addr); write5(DSKTOP, stkp)`.

(Use only the non-METAKERNEL path; the `#if 0` METAKERNEL branch is HP49.)

#### W2.4 Public API
```c
/* Push a complete RPL object (raw nibbles, prologue first, length `n`) onto
   stack level 1. 0 on success, <0 on failure (OOM / non-GX / bad object). */
int hp48_stack_push_object(const unsigned char *obj, int n);
```
Implementation: sanity-check `n` against the object's own prologue/length via the
R2 sizer; `addr = rpl_alloc_temp(n)`; if 0 fail; `store_n(addr, obj, n)`;
`rpl_push(addr)`. Optional conveniences built on top: `hp48_push_real(double)`,
`hp48_push_string(bytes,len)` (binio shows the string wrapper: `DOCSTR` prologue
`0x02A2C`, 5-nibble length, then data).

#### W2.5 Atomicity, safe point, mapping
- Call only between `hp48_run_slice()` calls (calc idle). Because the CPU does
  not run during the call, the whole alloc+push is atomic by construction — no
  interrupt or GC can observe a half-built state. This is the entire atomicity
  story, and it is why this is far less scary than it first looks.
- Force the GX RAM bank mapping for the whole operation (same `map_gx_ram` the
  read path uses) and restore it after, so `read_nibble`/`write_nibble` resolve
  the absolute system addresses.
- **Bonus — free offline push:** running these same primitives in the window
  between `hp48_load_state()` and `hp48_start()` *is* the safe "in-process W1"
  edit (CPU not started yet). So W2's code also delivers the recommended offline
  path with no extra machinery.

#### W2.6 Caveats / limitations (inherited from binio)
- **No garbage collection.** A push fails if the contiguous free gap
  `[RSKTOP, DSKTOP)` is too small, even when a GC would free enough. First cut
  mirrors this (clean OOM return); a fuller version would invoke the calc's GC
  first (harder — out of scope for v1).
- **GX-only.** Addresses are hard-coded; SX needs `TEMPTOP/RSKTOP/AVMEM_SX`, and
  the SX `AVMEM` address is still unknown. Gate under `HP48_GX_ONLY`; guard on
  `opt_gx` and fail safe otherwise.
- **Display lag.** The stack display refreshes on the calc's next interaction;
  the host may want to nudge it (e.g. a harmless key) after pushing.
- **No aliasing.** The pushed object is a fresh TEMPOB copy (unlike a `DUP`'d
  pointer).

#### W2.7 Validation
- Round-trip: read an object off the stack with the Phase R API, push it back,
  run a slice, and confirm `hp48_stack_depth()` grew by one and
  `hp48_stack_describe(1, ...)` decodes the same value.
- Robustness: after pushing, drive a few key ops (or run longer) and confirm no
  crash / GC fault — i.e. `AVMEM` and the manager pointers stayed consistent.

#### Lower-risk alternatives (no manager surgery)
If touching the manager pointers feels too risky, reuse the calc's *own*
allocator instead: inject as a **global variable** (store into a directory and
recall), or do **keyboard/command-line injection** (type the object's text form
+ ENTER via `hp48_press_key`). Slower and less general, but they can't corrupt
the heap.

### Shared prerequisites
- **GX manager addresses: known** (table above, from droid48). SX still needs its
  `TEMPTOP`/`RSKTOP`/`AVMEM` — the first two are derivable as the consecutive
  slots before `DSKTOP_SX`, but SX `AVMEM` needs a real lookup.
- `rpl_object_size()` (R2) — **done** (`rplstack.c`); WRITE reuses it to size the
  incoming object.

## Notes
- All new core code is in `libcalc48`, instance-aware via the `cpu` bridge; no
  SDL dependency. The W1 host-side tool needs no core changes at all.
- READ is **done** (`rplstack.c`, behind `HP48_GX_ONLY`). WRITE is now fully
  specified (W2): the droid48 recipe ports directly, the GX addresses are known,
  and R2's sizer is in place — the only genuinely open item for GX is choosing
  whether v1 handles GC (it need not). The same W2 primitives, run before
  `hp48_start()`, also give the safe offline (W1) push for free.
- Build the WRITE primitives in the same `HP48_GX_ONLY` module as the read API;
  guard on `opt_gx` and fail safe on non-GX.
