/*
 *  Inert debugger backend, compiled instead of debugger.c when the build sets
 *  HP48_WITH_DEBUGGER=OFF.
 *
 *  The interactive debugger (debugger.c) reads commands from stdin and is a
 *  terminal-bound development tool an embedded host (Android, Qt, a headless
 *  library) never wants.  When it is compiled out, the rest of the core and the
 *  front end still reference a few debugger globals and entry points; this file
 *  supplies inert versions so debug events (illegal/trap instructions, a user
 *  interrupt, breakpoints) are simply ignored and execution continues.
 */

#include "hp48.h"
#include "debugger.h"

/* State the emulator/front end read to gate timers, wakeups and the run loop. */
int enter_debugger = 0;
int in_debugger    = 0;
int exec_flags     = 0;

/* No breakpoints without the debugger. */
int
hp48_debug_check (void)
{
  return 0;
}

/* A debug event was raised but there is no debugger to service it: clear the
 * request and let the calculator keep running. */
int
debug (void)
{
  enter_debugger = 0;
  return 0;
}
