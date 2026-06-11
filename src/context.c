/*
 *  Emulator instance storage and the active-context pointer.
 *
 *  See hp48_state.h and REFACTOR_PLAN.md (Phase 2).  For now a single static
 *  instance is bound at program start, preserving the historical single-global
 *  behaviour; the Phase 4 public API will add create/destroy and rebind `cpu`.
 */

/* This file manipulates hp48_t fields by their real names, so suppress the
 * global->member bridge macros (which would rewrite field designators). */
#define HP48_STATE_NO_BRIDGE 1

#include "hp48.h"

/*
 *  Non-zero initial values that the migrated globals used to carry as their
 *  static initializers.  Everything else is zero-initialized.  When Phase 4
 *  adds hp48_create(), this list moves into an init function.
 */
static hp48_t default_instance = {
    .last_annunc_state    = -1,   /* lcd.c: force first annunciator redraw */
    .line_counter         = -1,   /* memory.c */
    .rom_is_new           = 1,    /* init.c */
    .first_press          = 1,    /* actions.c */

    /* emulate.c scheduler reload values */
    .sched_instr_rollover = SCHED_INSTR_ROLLOVER,
    .sched_receive        = SCHED_RECEIVE,
    .sched_adjtime        = SCHED_ADJTIME,
    .sched_timer1         = SCHED_TIMER1,
    .sched_timer2         = SCHED_TIMER2,
    .sched_statistics     = SCHED_STATISTICS,
    .sched_display        = SCHED_NEVER,
};

hp48_t *cpu = &default_instance;
