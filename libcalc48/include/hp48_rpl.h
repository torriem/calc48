/*
 *  RPL user-stack read API for the emulator core (Phase R of
 *  docs/stack_io_plan.md).
 *
 *  These calls let a host inspect the objects currently on the calculator's
 *  RPL user stack -- an alternative way to get data out of the emulator besides
 *  the plug-in ports.  They are read-only and side-effect free; call them at a
 *  safe point (the calc idle, between hp48_run_slice() calls), not mid-run.
 *
 *  GX ONLY.  The stack and memory-manager pointers this uses are hard-coded for
 *  the HP 48 G/GX, so the module is built only when the library is configured
 *  for a GX target (CMake HP48_GX_ONLY).  On a non-GX instance the calls fail
 *  safe (depth 0 / -1).  See docs/stack_io_plan.md.
 *
 *  Addresses are absolute Saturn nibble addresses (20-bit).  "Nibble buffers"
 *  are one nibble per byte (low nibble used), matching the lcd / memory
 *  conventions elsewhere in the core; the least-significant nibble comes first.
 *
 *  This header pulls in no SDL and no stdio types.
 */

#ifndef _HP48_RPL_H
#define _HP48_RPL_H 1

#include <stdint.h>

/* Number of objects on the RPL user stack (level 1 = top).  0 if empty, and
 * also 0 on a non-GX instance or if the pointers look invalid. */
extern int      hp48_stack_depth(void);

/* Absolute address of the object at stack `level` (1 = top).  0 if `level` is
 * out of range (1..hp48_stack_depth()). */
extern uint32_t hp48_stack_addr(int level);

/* The 5-nibble prologue (type id) of the object at `addr`. */
extern uint32_t hp48_object_prolog(uint32_t addr);

/* Total length in nibbles of the object at `addr` (prologue + body), or < 0 if
 * the object could not be sized (e.g. recursion guard tripped on bad data).
 * An unknown prologue yields 5 (the prologue alone). */
extern int      hp48_object_size(uint32_t addr);

/* Copy the object at `addr` as raw nibbles (one per byte) into `out`.  Returns
 * the object's full nibble length; if that exceeds `max`, only `max` nibbles are
 * written and the caller should retry with a larger buffer.  < 0 on error. */
extern int      hp48_read_object(uint32_t addr, unsigned char *out, int max);

/* Human-readable decode of the object at stack `level` (1 = top), like the
 * debugger's stack view, written as a NUL-terminated string into `out` (at most
 * `max` bytes incl. the NUL).  Returns the string length written, or < 0 on
 * error (bad level / non-GX). */
extern int      hp48_stack_describe(int level, char *out, int max);

/*
 *  Push a complete RPL object onto stack level 1.  `obj` is `n` raw nibbles
 *  (one per byte), prologue first -- the same packing hp48_read_object()
 *  produces, so an object read from one calc can be pushed onto another.  A
 *  copy is allocated in the temporary-object area and the stack grown to point
 *  at it.  Returns 0 on success, < 0 on failure (out of memory, non-GX
 *  instance, or a malformed object).
 *
 *  Call only at a safe point -- the calculator idle, between hp48_run_slice()
 *  calls -- so the allocation + pointer updates are atomic w.r.t. execution.
 *  No garbage collection is performed: a push can fail for lack of contiguous
 *  free memory even when a GC would have made room.
 */
extern int      hp48_stack_push_object(const unsigned char *obj, int n);

#endif /* !_HP48_RPL_H */
