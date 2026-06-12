/*
 *  Storage interface for the emulator core.
 *
 *  libhp48 performs NO filesystem access of its own.  Every persistent
 *  resource -- the ROM image and the saved-calculator files (hp48, ram,
 *  port1, port2) -- is moved in and out as an opaque byte blob through these
 *  callbacks, which the host (the UI / front end) implements.  This lets the
 *  emulator run on platforms where direct path-based file I/O is unavailable
 *  or restricted (Android asset managers / content URIs, iOS sandboxes,
 *  consoles, WASM, ...).
 *
 *  A desktop front end can install the bundled stdio provider (hp48_io_stdio.c)
 *  to back these with ordinary files; everyone else supplies their own.
 *
 *  This header pulls in no SDL and no stdio types.
 */

#ifndef _HP48_IO_H
#define _HP48_IO_H 1

#include <stddef.h>   /* size_t */

/* Resource names passed to the callbacks. */
#define HP48_RES_ROM	"rom"
#define HP48_RES_STATE	"hp48"
#define HP48_RES_RAM	"ram"
#define HP48_RES_PORT1	"port1"
#define HP48_RES_PORT2	"port2"

typedef struct hp48_io_t {
  void *user;

  /*
   *  Load resource `name` fully into memory.  On success, set *data to a
   *  buffer the library will free() with the C runtime, set *len to its
   *  length, and return 0.  Return non-zero if the resource is absent or
   *  unreadable (the library treats optional resources -- the ports -- as
   *  simply not present, and required ones as a load error).
   */
  int (*load)(void *user, const char *name, unsigned char **data, size_t *len);

  /*
   *  Persist `len` bytes of `data` as resource `name`.  Return 0 on success.
   */
  int (*save)(void *user, const char *name, const unsigned char *data,
              size_t len);
} hp48_io_t;

/* Install the host's storage callbacks on the active emulator instance. */
extern void hp48_set_io(const hp48_io_t *io);

/*
 *  Bundled stdio file provider (hp48_io_stdio.c) -- optional; desktop only.
 *  Installs a storage provider that backs each resource with a file named
 *  <dir>/<name>.  Not part of a minimal/embedded build (e.g. Android), where
 *  the host installs its own hp48_io_t via hp48_set_io().
 */
extern void hp48_io_stdio(const char *dir);

#endif /* !_HP48_IO_H */
