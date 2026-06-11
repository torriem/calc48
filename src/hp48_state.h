/*
 *  Per-instance emulator state (Phase 2 of REFACTOR_PLAN.md).
 *
 *  All mutable emulator state is being migrated from scattered file-scope
 *  globals into this single struct, so the emulator can be instantiated and
 *  driven re-entrantly (e.g. from Python or a Qt app).
 *
 *  Access is bridged through the active-context pointer `cpu` plus the macros
 *  below: code that used to reference a global (e.g. `saturn`) keeps compiling
 *  unchanged, because the macro rewrites the bare name into `cpu->saturn`.
 *  For this to be safe, the corresponding `extern` declaration and definition
 *  of each migrated global must be removed (otherwise the macro would rewrite
 *  the declaration itself); the compiler flags any name that still collides.
 *
 *  This header is included from the end of hp48.h, so every translation unit
 *  that includes hp48.h automatically picks up the struct and the macros.
 */

#ifndef _HP48_STATE_H
#define _HP48_STATE_H 1

#include "hp48.h"   /* saturn_t, display_t and friends */

typedef struct hp48_t {

  /* Saturn CPU + peripheral register file (the bulk of emulator state). */
  saturn_t  saturn;

  /* --- LCD / display (lcd.c) --- */
  display_t     display;
  unsigned char disp_buf[DISP_ROWS][NIBS_PER_BUFFER_ROW];
  unsigned char lcd_buffer[DISP_ROWS][NIBS_PER_BUFFER_ROW];
  int           last_annunc_state;

} hp48_t;

/*
 *  The currently-active emulator instance.  Today a single static instance is
 *  bound at startup (see context.c); the Phase 4 public API will let callers
 *  create/destroy instances and rebind this at each API entry point.
 */
extern hp48_t *cpu;

/* ------------------------------------------------------------------ */
/* global name -> active-instance member bridges                      */
/* ------------------------------------------------------------------ */
/* context.c defines HP48_STATE_NO_BRIDGE so it can manipulate the struct
 * fields by their real names (the bridge would otherwise rewrite a field
 * designator like `.saturn` into `.(cpu->saturn)`). */
#ifndef HP48_STATE_NO_BRIDGE

#define saturn            (cpu->saturn)

/* display (lcd.c). NB: `display` is NOT bridged by macro -- it collides with
 * an SDL_video.h parameter name. Its (few) call sites use cpu->display
 * directly instead. */
#define disp_buf          (cpu->disp_buf)
#define lcd_buffer        (cpu->lcd_buffer)
#define last_annunc_state (cpu->last_annunc_state)

#endif /* !HP48_STATE_NO_BRIDGE */

#endif /* !_HP48_STATE_H */
