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

#include <stdlib.h>
#include <string.h>

/*
 *  Non-zero initial values that the migrated globals used to carry as their
 *  static initializers.  Everything else is zero-initialized.  Keep this in
 *  sync with hp48_init_defaults() below (this static initializer seeds the
 *  implicit instance the SDL front end uses; hp48_init_defaults() seeds the
 *  heap instances created via hp48_create()).
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

/*
 *  Program name used in core diagnostic messages.  Owned by the core so that
 *  libcalc48 links standalone; the front end may overwrite it (e.g. from argv).
 */
char *progname = "x48";

/* Install the front end's UI callbacks on the active instance. */
void
hp48_set_ui(const hp48_ui_t *ui)
{
  if (ui)
    cpu->ui = *ui;
}

/* Install the host's storage callbacks on the active instance. */
void
hp48_set_io(const hp48_io_t *io)
{
  if (io)
    cpu->io = *io;
}

/*
 *  Initialise a freshly-allocated instance to the same defaults the static
 *  default_instance carries.  Keep in sync with the initializer above.
 */
void
hp48_init_defaults(hp48_t *h)
{
  memset(h, 0, sizeof(*h));

  h->last_annunc_state    = -1;
  h->line_counter         = -1;
  h->rom_is_new           = 1;
  h->first_press          = 1;

  h->sched_instr_rollover = SCHED_INSTR_ROLLOVER;
  h->sched_receive        = SCHED_RECEIVE;
  h->sched_adjtime        = SCHED_ADJTIME;
  h->sched_timer1         = SCHED_TIMER1;
  h->sched_timer2         = SCHED_TIMER2;
  h->sched_statistics     = SCHED_STATISTICS;
  h->sched_display        = SCHED_NEVER;
}

/* Select which instance the bridged globals (saturn, display, ...) resolve to. */
void
hp48_set_active(hp48_t *h)
{
  if (h)
    cpu = h;
}

/*
 *  Allocate a new emulator instance, initialise it to defaults, and make it
 *  the active instance.  Returns NULL on allocation failure.  The caller then
 *  loads a ROM/state and calls hp48_start() before hp48_run_slice().
 */
hp48_t *
hp48_create(void)
{
  hp48_t *h = (hp48_t *)malloc(sizeof(hp48_t));

  if (h) {
    hp48_init_defaults(h);
    cpu = h;
  }
  return h;
}

/*
 *  Free an instance and the emulator-owned memory buffers it holds.  If the
 *  destroyed instance was active, the active pointer falls back to the static
 *  default instance so the bridges stay valid.
 */
void
hp48_destroy(hp48_t *h)
{
  if (!h)
    return;

  if (h->saturn.rom)   { free(h->saturn.rom);   h->saturn.rom   = (void *)0; }
  if (h->saturn.ram)   { free(h->saturn.ram);   h->saturn.ram   = (void *)0; }
  if (h->saturn.port1) { free(h->saturn.port1); h->saturn.port1 = (void *)0; }
  if (h->saturn.port2) { free(h->saturn.port2); h->saturn.port2 = (void *)0; }

  if (cpu == h)
    cpu = &default_instance;

  if (h != &default_instance)
    free(h);
}
