/*
 *  UI / rendering interface for the emulator core (Phase 3 of
 *  REFACTOR_PLAN.md).
 *
 *  The emulator core (libhp48) must not depend on SDL -- or on any particular
 *  UI toolkit -- so everything the core needs from the front end is expressed
 *  here as plain-C callbacks.  A front end (the SDL app, a Qt app, a Python
 *  binding, ...) fills in an hp48_ui_t and registers it with hp48_set_ui().
 *
 *  This header contains no SDL types and pulls in no SDL headers, so the core
 *  builds without SDL installed.
 */

#ifndef _HP48_UI_H
#define _HP48_UI_H 1

/*
 *  LCD geometry shared between the emulator and whatever renders it.  The
 *  emulator computes `offset` and `lines` from the Saturn display registers;
 *  the front end sets `w`, `h` and `mapped` (whether the output is visible).
 *  Deliberately free of SDL types.
 */
typedef struct disp_t {
  unsigned int w, h;
  short        mapped;
  int          offset;
  int          lines;
} disp_t;

/*
 *  Callbacks supplied by the front end.  Any callback may be NULL; the core
 *  checks before calling, so a headless instance needs no UI at all.
 *  `user` is an opaque pointer passed back to every callback.
 */
typedef struct hp48_ui_t {
  void *user;

  /* Draw one 4-bit LCD nibble `val` at pixel column/row (x, y). */
  void (*draw_nibble)(void *user, int x, int y, int val);

  /* Redraw the annunciators given the raw annunciator bitmask. */
  void (*draw_annunc)(void *user, int annunc);

  /* The LCD contrast level changed. */
  void (*adjust_contrast)(void *user, int contrast);

  /* Report serial wire / IR pseudo-terminal names to the user. */
  void (*show_connections)(void *user, const char *wire, const char *ir);

  /* Tear down the front end (was exit_x48); `tell` mirrors the old arg. */
  void (*exit)(void *user, int tell);

} hp48_ui_t;

/* Install the front end's callbacks on the active emulator instance. */
extern void hp48_set_ui(const hp48_ui_t *ui);

#endif /* !_HP48_UI_H */
