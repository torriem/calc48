/*
 *  Bundled stdio storage provider for libcalc48.
 *
 *  This is the *default* implementation of the hp48_io_t storage interface for
 *  desktop platforms: it backs each resource ("rom", "hp48", "ram", "port1",
 *  "port2") with an ordinary file named <dir>/<name>.  It is the ONLY part of
 *  the library that touches the filesystem, and it is a separate translation
 *  unit so a restricted build (Android, iOS, a console, WASM) can leave it out
 *  and supply its own hp48_io_t instead.
 *
 *  The base directory is not a global: it is passed in by whoever installs the
 *  provider (hp48_io_stdio(dir)) and carried in hp48_io_t.user, handed back to
 *  every callback.  The caller owns the string and must keep it alive for as
 *  long as the provider is installed (the SDL front end keeps it for the life
 *  of the process).
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <sys/stat.h>
#include <sys/types.h>

#include "hp48.h"
#include "resources.h"   /* quiet, progname (diagnostics) */
#include "romio.h"

/* hp48_io_t.load: slurp <dir>/<name> into a malloc'd buffer. */
static int
stdio_load(void *user, const char *name, unsigned char **data, size_t *len)
{
  const char    *dir = (const char *)user;
  char           fnam[1200];
  FILE          *fp;
  long           sz;
  unsigned char *buf;

  snprintf(fnam, sizeof(fnam), "%s/%s", dir, name);
  if (NULL == (fp = fopen(fnam, "rb")))
    return -1;

  if (fseek(fp, 0, SEEK_END) < 0 || (sz = ftell(fp)) < 0)
    {
      fclose(fp);
      return -1;
    }
  rewind(fp);

  if (NULL == (buf = (unsigned char *)malloc(sz ? (size_t)sz : 1)))
    {
      fclose(fp);
      return -1;
    }
  if (sz && fread(buf, 1, (size_t)sz, fp) != (size_t)sz)
    {
      free(buf);
      fclose(fp);
      return -1;
    }
  fclose(fp);

  *data = buf;
  *len = (size_t)sz;
  return 0;
}

/* hp48_io_t.save: write `data` to <dir>/<name>, creating the dir. */
static int
stdio_save(void *user, const char *name, const unsigned char *data, size_t len)
{
  const char *dir = (const char *)user;
  char        fnam[1200];
  FILE       *fp;
  struct stat st;

  if (stat(dir, &st) == -1 && errno == ENOENT)
    mkdir(dir, 0777);

  snprintf(fnam, sizeof(fnam), "%s/%s", dir, name);
  if (NULL == (fp = fopen(fnam, "wb")))
    {
      if (!quiet)
        fprintf(stderr, "%s: can't open %s for writing\n", progname, fnam);
      return -1;
    }
  if (len && fwrite(data, 1, len, fp) != len)
    {
      fclose(fp);
      return -1;
    }
  fclose(fp);
  return 0;
}

/*
 *  Install the stdio provider rooted at directory `dir`.  `dir` is stored in
 *  io.user and used verbatim, so it must be an absolute (or otherwise valid)
 *  path and must outlive the installed provider -- the caller owns it.
 */
void
hp48_io_stdio(const char *dir)
{
  hp48_io_t io;

  memset(&io, 0, sizeof(io));
  io.user = (void *)dir;
  io.load = stdio_load;
  io.save = stdio_save;
  hp48_set_io(&io);
}

/* ---- path-based conveniences (desktop) -------------------------------- */

int
hp48_load_state(const char *dir)
{
  hp48_io_stdio(dir);
  return read_files();
}

int
hp48_save_state(const char *dir)
{
  hp48_io_stdio(dir);
  return write_files() ? 0 : -1;
}

/* Load just a ROM image from an explicit path (may be outside the state dir). */
int
hp48_load_rom(const char *path)
{
  FILE *fp;
  int   rv;

  if (NULL == (fp = fopen(path, "rb")))
    return -1;
  rv = read_rom_file(fp, path, &saturn.rom, (int *)&rom_size) ? 0 : -1;
  fclose(fp);
  return rv;
}

/* Fresh boot: initialise CPU state, load the ROM at `path`, zero RAM. */
int
hp48_init_from_rom(const char *path)
{
  FILE *fp;
  int   rv;

  if (NULL == (fp = fopen(path, "rb")))
    return -1;
  init_saturn();
  rv = read_rom(fp, path) ? 0 : -1;
  fclose(fp);
  return rv;
}
