/*
 *  Emulator instance storage and the active-context pointer.
 *
 *  See hp48_state.h and REFACTOR_PLAN.md (Phase 2).  For now a single static
 *  instance is bound at program start, preserving the historical single-global
 *  behaviour; the Phase 4 public API will add create/destroy and rebind `cpu`.
 */

#include "hp48.h"

static hp48_t default_instance;

hp48_t *cpu = &default_instance;
