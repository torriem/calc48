/*
 *  Bundled stdio storage provider for libhp48.
 *
 *  This is the *default* implementation of the hp48_io_t storage interface for
 *  desktop platforms: it backs each resource ("rom", "hp48", "ram", "port1",
 *  "port2") with an ordinary file named <dir>/<name>.  It is the ONLY part of
 *  the emulator that touches the filesystem, and it is deliberately a separate
 *  translation unit so a restricted build (Android, iOS, a console, WASM) can
 *  leave it out and supply its own hp48_io_t instead.
 *
 *  It also provides the path-based convenience entry points that the desktop
 *  front end uses (hp48_load_state/save_state/load_rom/init_from_rom and the
 *  init_emulator/exit_emulator orchestrators).
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <unistd.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <pwd.h>

#include "hp48.h"
#include "resources.h"
#include "romio.h"

/* Resolved base directory the resources live in. */
static char stdio_dir[1024];

/*
 *  Resolve `home` (absolute, or relative to $HOME / the password database)
 *  into an absolute directory path, with no trailing slash.  Moved here from
 *  init.c -- it is filesystem/desktop policy, not emulator logic.
 */
static void
resolve_home(char *path, const char *home)
{
  char          *p;
  struct passwd *pwd;

  if (home[0] == '/')
    {
      strcpy(path, home);
      return;
    }

  p = getenv("HOME");
  if (p)
    {
      strcpy(path, p);
      strcat(path, "/");
    }
  else if ((pwd = getpwuid(getuid())) != NULL)
    {
      strcpy(path, pwd->pw_dir);
      strcat(path, "/");
    }
  else
    {
      if (!quiet)
        fprintf(stderr,
                "%s: can't figure out your home directory, trying /tmp\n",
                progname);
      strcpy(path, "/tmp/");
    }
  strcat(path, home);
}

/* hp48_io_t.load: slurp <stdio_dir>/<name> into a malloc'd buffer. */
static int
stdio_load(void *user, const char *name, unsigned char **data, size_t *len)
{
  char           fnam[1200];
  FILE          *fp;
  long           sz;
  unsigned char *buf;

  (void)user;

  snprintf(fnam, sizeof(fnam), "%s/%s", stdio_dir, name);
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

/* hp48_io_t.save: write `data` to <stdio_dir>/<name>, creating the dir. */
static int
stdio_save(void *user, const char *name, const unsigned char *data, size_t len)
{
  char        fnam[1200];
  FILE       *fp;
  struct stat st;

  (void)user;

  if (stat(stdio_dir, &st) == -1 && errno == ENOENT)
    mkdir(stdio_dir, 0777);

  snprintf(fnam, sizeof(fnam), "%s/%s", stdio_dir, name);
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

/* Install the stdio provider rooted at directory `dir`. */
void
hp48_io_stdio(const char *dir)
{
  hp48_io_t io;

  resolve_home(stdio_dir, dir);

  memset(&io, 0, sizeof(io));
  io.user = NULL;
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

/* ---- desktop orchestrators (moved from init.c) ------------------------ */

int
init_emulator(void)
{
  if (!initialize)                       /* not a complete (fresh) init */
    if (hp48_load_state(homeDirectory) == 0)
      {
        if (resetOnStartup)
          saturn.PC = 0x00000;
        return 0;
      }

  return hp48_init_from_rom(romFileName);
}

int
exit_emulator(void)
{
  hp48_save_state(homeDirectory);
  return 1;
}
