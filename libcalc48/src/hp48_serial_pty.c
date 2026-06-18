/*
 *  Bundled pseudo-terminal / device serial provider for libcalc48.
 *
 *  This is the *default* hp48_serial_t transport for desktop platforms.  It is
 *  the part of the serial support that touches host facilities (PTYs, tty
 *  devices, termios), so it lives in its own translation unit, gated behind the
 *  HP48_WITH_SERIAL build option, exactly like the stdio storage provider.  A
 *  restricted target can leave it out and install its own hp48_serial_t.
 *
 *    - the WIRE channel is a freshly allocated pseudo-terminal; its slave path
 *      is reported as the connection name, so a host program (Kermit, a
 *      terminal) can attach to it and talk to the emulated calculator.
 *    - the IR channel is the real tty device passed to hp48_serial_pty().
 *
 *  De-globalized: it takes its configuration (which channels to open, the IR
 *  device path, a verbosity flag) as arguments rather than reaching into the
 *  resources globals, and prints with a fixed prefix.  Single connection set
 *  (file-static), matching the original behaviour.
 */

#include "global.h"

#include <stdio.h>
#include <stdlib.h>   /* grantpt, unlockpt, ptsname_r (with _GNU_SOURCE) */
#include <string.h>
#include <fcntl.h>
#include <errno.h>
#if defined(HPUX) || defined(CSRG_BASED)
#  include <sys/ioctl.h>
#endif
#include <unistd.h>
#include <termios.h>
#ifdef SOLARIS
#  include <sys/stream.h>
#  include <sys/stropts.h>
#  include <sys/termios.h>
#endif

#include "hp48_serial.h"

/* Per-channel transport state for this provider. */
static struct {
  int  open_wire;            /* config: allocate a PTY for the wire channel  */
  const char *ir_device;     /* config: tty device for the IR channel (or 0) */
  int  verbose;

  int  wire_fd;              /* PTY master (the side the emulator uses)       */
  int  wire_slave;           /* PTY slave (kept open so the master stays sane)*/
  char wire_name[128];       /* slave device path, reported to the UI         */

  int  ir_fd;                /* IR tty device fd                              */
  char ir_name[128];
} pty;

/* Apply the 8250-style raw-mode line settings the calculator expects. */
static void
set_raw_mode(int fd)
{
  struct termios ttybuf;
  int n;

#if defined(TCSANOW)
  if (tcgetattr(fd, &ttybuf) < 0)
    return;
#else
  if (ioctl(fd, TCGETS, (char *)&ttybuf) < 0)
    return;
#endif

  ttybuf.c_lflag = 0;
  ttybuf.c_iflag = 0;
  ttybuf.c_oflag = 0;
  ttybuf.c_cflag = B9600 | CS8 | CLOCAL | CREAD;
  for (n = 0; n < NCCS; n++)
    ttybuf.c_cc[n] = 0;
  ttybuf.c_cc[VTIME] = 0;
  ttybuf.c_cc[VMIN]  = 1;

#if defined(TCSANOW)
  tcsetattr(fd, TCSANOW, &ttybuf);
#else
  ioctl(fd, TCSETS, (char *)&ttybuf);
#endif
}

/* Open the wire-channel PTY.  Returns the master fd, or -1. */
static int
open_wire_pty(void)
{
  char *p;
  int   c, n;
  char  tty_dev_name[128];

  pty.wire_fd = -1;
  pty.wire_slave = -1;
  pty.wire_name[0] = '\0';

#if defined(IRIX)
  if ((p = _getpty(&pty.wire_fd, O_RDWR | O_EXCL | O_NDELAY, 0666, 0)) == NULL)
    {
      pty.wire_fd = -1;
      return -1;
    }
  if ((pty.wire_slave = open(p, O_RDWR | O_NDELAY, 0666)) < 0)
    {
      close(pty.wire_fd);
      pty.wire_fd = -1;
      return -1;
    }
  strncpy(pty.wire_name, p, sizeof(pty.wire_name) - 1);
#elif defined(SOLARIS)
  if ((pty.wire_fd = open("/dev/ptmx", O_RDWR | O_NONBLOCK, 0666)) < 0)
    return -1;
  grantpt(pty.wire_fd);
  unlockpt(pty.wire_fd);
  p = ptsname(pty.wire_fd);
  strncpy(tty_dev_name, p, sizeof(tty_dev_name) - 1);
  tty_dev_name[sizeof(tty_dev_name) - 1] = '\0';
  if ((pty.wire_slave = open(tty_dev_name, O_RDWR | O_NDELAY, 0666)) < 0)
    {
      close(pty.wire_fd);
      pty.wire_fd = -1;
      return -1;
    }
  ioctl(pty.wire_slave, I_PUSH, "ptem");
  ioctl(pty.wire_slave, I_PUSH, "ldterm");
  strncpy(pty.wire_name, tty_dev_name, sizeof(pty.wire_name) - 1);
#elif defined(LINUX)
  /* Unix98 PTY (preferred) */
  if ((pty.wire_fd = open("/dev/ptmx", O_RDWR | O_NONBLOCK, 0666)) >= 0)
    {
      grantpt(pty.wire_fd);
      unlockpt(pty.wire_fd);
      if (ptsname_r(pty.wire_fd, tty_dev_name, sizeof(tty_dev_name)))
        {
          close(pty.wire_fd);
          pty.wire_fd = -1;
          return -1;
        }
      if ((pty.wire_slave = open(tty_dev_name, O_RDWR | O_NDELAY, 0666)) < 0)
        {
          close(pty.wire_fd);
          pty.wire_fd = -1;
          return -1;
        }
      strncpy(pty.wire_name, tty_dev_name, sizeof(pty.wire_name) - 1);
    }
  else
    {
      /* BSD PTY (legacy) */
      c = 'p';
      do
        {
          for (n = 0; n < 16; n++)
            {
              sprintf(tty_dev_name, "/dev/pty%c%x", c, n);
              if ((pty.wire_fd = open(tty_dev_name,
                                      O_RDWR | O_EXCL | O_NDELAY, 0666)) >= 0)
                {
                  pty.wire_slave = pty.wire_fd;
                  sprintf(tty_dev_name, "/dev/tty%c%x", c, n);
                  strncpy(pty.wire_name, tty_dev_name,
                          sizeof(pty.wire_name) - 1);
                  break;
                }
            }
          c++;
        }
      while ((pty.wire_fd < 0) && (errno != ENOENT));
    }
#else
  /* SUNOS, HPUX */
  c = 'p';
  do
    {
      for (n = 0; n < 16; n++)
        {
          sprintf(tty_dev_name, "/dev/ptyp%x", n);
          if ((pty.wire_fd = open(tty_dev_name,
                                  O_RDWR | O_EXCL | O_NDELAY, 0666)) >= 0)
            {
              sprintf(tty_dev_name, "/dev/tty%c%x", c, n);
              if ((pty.wire_slave = open(tty_dev_name,
                                         O_RDWR | O_NDELAY, 0666)) < 0)
                {
                  close(pty.wire_fd);
                  pty.wire_fd = -1;
                  return -1;
                }
              strncpy(pty.wire_name, tty_dev_name, sizeof(pty.wire_name) - 1);
              break;
            }
        }
      c++;
    }
  while ((pty.wire_fd < 0) && (errno != ENOENT));
#endif

  if (pty.wire_fd < 0)
    return -1;

  pty.wire_name[sizeof(pty.wire_name) - 1] = '\0';
  if (pty.wire_slave >= 0)
    set_raw_mode(pty.wire_slave);
  return pty.wire_fd;
}

/* ---- hp48_serial_t callbacks ------------------------------------------ */

static int
pty_open(void *user, int channel, const char **name)
{
  (void)user;

  if (channel == HP48_SERIAL_WIRE)
    {
      if (!pty.open_wire)
        return -1;
      if (open_wire_pty() < 0)
        return -1;
      if (pty.verbose)
        printf("x48: wire connection on %s\n", pty.wire_name);
      if (name)
        *name = pty.wire_name;
      return pty.wire_fd;
    }

  /* HP48_SERIAL_IR: a real tty device */
  pty.ir_fd = -1;
  pty.ir_name[0] = '\0';
  if (!pty.ir_device)
    return -1;
  if ((pty.ir_fd = open(pty.ir_device, O_RDWR | O_NDELAY)) < 0)
    return -1;
  set_raw_mode(pty.ir_fd);
  strncpy(pty.ir_name, pty.ir_device, sizeof(pty.ir_name) - 1);
  pty.ir_name[sizeof(pty.ir_name) - 1] = '\0';
  if (pty.verbose)
    printf("x48: IR connection on %s\n", pty.ir_name);
  if (name)
    *name = pty.ir_name;
  return pty.ir_fd;
}

/* Map the calculator's baud code (0..7) to a termios speed and apply it. */
static void
pty_configure(void *user, int channel, int baud)
{
  int fd = (channel == HP48_SERIAL_IR) ? pty.ir_fd : pty.wire_fd;
  struct termios ttybuf;
  speed_t speed;

  (void)user;
  if (fd < 0)
    return;

#if defined(TCSANOW)
  if (tcgetattr(fd, &ttybuf) < 0)
    return;
#else
  if (ioctl(fd, TCGETS, (char *)&ttybuf) < 0)
    return;
#endif

  switch (baud & 0x7)
    {
      case 0:  speed = B1200; break;   /* 1200  */
#ifdef B1920
      case 1:  speed = B1920; break;   /* 1920  */
#endif
      case 2:  speed = B2400; break;   /* 2400  */
#ifdef B3840
      case 3:  speed = B3840; break;   /* 3840  */
#endif
      case 4:  speed = B4800; break;   /* 4800  */
#ifdef B7680
      case 5:  speed = B7680; break;   /* 7680  */
#endif
      case 6:  speed = B9600; break;   /* 9600  */
#ifdef B15360
      case 7:  speed = B15360; break;  /* 15360 */
#endif
      default: speed = B9600; break;
    }

#if defined(__APPLE__) || defined(CSRG_BASED)
  cfsetispeed(&ttybuf, speed);
  cfsetospeed(&ttybuf, speed);
#else
  ttybuf.c_cflag &= ~CBAUD;
  ttybuf.c_cflag |= speed;
#endif

#if defined(TCSANOW)
  tcsetattr(fd, TCSANOW, &ttybuf);
#else
  ioctl(fd, TCSETS, (char *)&ttybuf);
#endif
}

static int
pty_read(void *user, int channel, unsigned char *buf, int max)
{
  int fd = (channel == HP48_SERIAL_IR) ? pty.ir_fd : pty.wire_fd;
  int n;

  (void)user;
  if (fd < 0)
    return 0;

  /* fds are O_NDELAY/O_NONBLOCK, so this returns immediately. */
  n = (int)read(fd, buf, (size_t)max);
  if (n < 0)
    return 0;            /* EAGAIN / would-block: nothing ready right now */
  return n;
}

static int
pty_write(void *user, int channel, const unsigned char *buf, int n)
{
  int fd = (channel == HP48_SERIAL_IR) ? pty.ir_fd : pty.wire_fd;
  int w;

  (void)user;
  if (fd < 0)
    return -1;

  w = (int)write(fd, buf, (size_t)n);
  if (w < 0)
    return 0;            /* drop on would-block, as the UART expects */
  return w;
}

static void
pty_close(void *user, int channel)
{
  (void)user;
  if (channel == HP48_SERIAL_WIRE)
    {
      if (pty.wire_slave >= 0 && pty.wire_slave != pty.wire_fd)
        close(pty.wire_slave);
      if (pty.wire_fd >= 0)
        close(pty.wire_fd);
      pty.wire_fd = pty.wire_slave = -1;
    }
  else
    {
      if (pty.ir_fd >= 0)
        close(pty.ir_fd);
      pty.ir_fd = -1;
    }
}

/*
 *  Install the PTY/device provider.  `use_wire` allocates a wire PTY;
 *  `ir_device` (or NULL) is the IR tty path; `verbose` prints the names.
 */
void
hp48_serial_pty(int use_wire, const char *ir_device, int verbose)
{
  hp48_serial_t s;

  memset(&pty, 0, sizeof(pty));
  pty.open_wire  = use_wire;
  pty.ir_device  = ir_device;
  pty.verbose    = verbose;
  pty.wire_fd    = -1;
  pty.wire_slave = -1;
  pty.ir_fd      = -1;

  memset(&s, 0, sizeof(s));
  s.open      = pty_open;
  s.configure = pty_configure;
  s.read      = pty_read;
  s.write     = pty_write;
  s.close     = pty_close;
  hp48_set_serial(&s);
}
