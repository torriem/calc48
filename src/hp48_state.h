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

  /* --- instruction loop / event scheduler (emulate.c) --- */
  long          jumpaddr;
  unsigned long instructions;
  unsigned long old_instr;
  int           rece_instr;
  int           device_check;
  int           adj_time_pending;
  int           set_t1;
  long          schedule_event;
  long          sched_instr_rollover;
  long          sched_receive;
  long          sched_adjtime;
  long          sched_timer1;
  long          sched_timer2;
  long          sched_statistics;
  long          sched_display;
  unsigned long t1_i_per_tick;
  unsigned long t2_i_per_tick;
  unsigned long s_1;
  unsigned long s_16;
  unsigned long old_s_1;
  unsigned long old_s_16;
  unsigned long delta_t_1;
  unsigned long delta_t_16;
  unsigned long delta_i;
  word_64       run;

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

/* scheduler / instruction loop (emulate.c). NB: `run` is NOT bridged -- it
 * collides with a local and a struct member in timer.c; its emulate.c call
 * sites use cpu->run directly. */
#define jumpaddr              (cpu->jumpaddr)
#define instructions          (cpu->instructions)
#define old_instr             (cpu->old_instr)
#define rece_instr            (cpu->rece_instr)
#define device_check          (cpu->device_check)
#define adj_time_pending      (cpu->adj_time_pending)
#define set_t1                (cpu->set_t1)
#define schedule_event        (cpu->schedule_event)
#define sched_instr_rollover  (cpu->sched_instr_rollover)
#define sched_receive         (cpu->sched_receive)
#define sched_adjtime         (cpu->sched_adjtime)
#define sched_timer1          (cpu->sched_timer1)
#define sched_timer2          (cpu->sched_timer2)
#define sched_statistics      (cpu->sched_statistics)
#define sched_display         (cpu->sched_display)
#define t1_i_per_tick         (cpu->t1_i_per_tick)
#define t2_i_per_tick         (cpu->t2_i_per_tick)
#define s_1                   (cpu->s_1)
#define s_16                  (cpu->s_16)
#define old_s_1               (cpu->old_s_1)
#define old_s_16              (cpu->old_s_16)
#define delta_t_1             (cpu->delta_t_1)
#define delta_t_16            (cpu->delta_t_16)
#define delta_i               (cpu->delta_i)

#endif /* !HP48_STATE_NO_BRIDGE */

#endif /* !_HP48_STATE_H */
