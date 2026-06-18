/*
 *  Bundled socket serial provider for libcalc48.
 *
 *  An alternative hp48_serial_t transport that backs each UART channel with a
 *  socket instead of a pseudo-terminal.  Handy where PTYs are awkward or absent
 *  and for wiring tools (or two emulator instances) together over a network.
 *  Like the PTY provider it lives in its own translation unit, gated behind
 *  HP48_WITH_SERIAL.
 *
 *  Each channel takes a spec string (NULL = unplugged):
 *    "listen:PORT"        -- TCP: wait for an inbound connection on PORT
 *    "connect:HOST:PORT"  -- TCP: connect out to HOST:PORT
 *    "unix:PATH"          -- connect to a Unix-domain socket at PATH
 *    "unix-listen:PATH"   -- create and listen on a Unix-domain socket at PATH
 *
 *  In a listen mode the channel reports itself connected as soon as the
 *  listening socket is up; the peer connection is accepted lazily (non-blocking)
 *  on the first read/write, and re-accepted if the peer disconnects.
 */

#include "global.h"

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <sys/types.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <arpa/inet.h>
#include <netdb.h>
#include <sys/un.h>

#include "hp48_serial.h"

enum sock_mode { SOCK_NONE = 0, SOCK_LISTEN, SOCK_CONNECT };

/* Per-channel socket state. */
typedef struct {
  enum sock_mode mode;
  int  listen_fd;     /* listening socket (LISTEN mode), else -1            */
  int  conn_fd;       /* the live peer connection, else -1                  */
  char name[128];
} sock_chan;

static sock_chan chan[2];   /* indexed by HP48_SERIAL_WIRE / _IR */

static void
set_nonblock(int fd)
{
  int fl = fcntl(fd, F_GETFL, 0);
  if (fl >= 0)
    fcntl(fd, F_SETFL, fl | O_NONBLOCK);
}

/* Open a TCP listening socket on `port`.  Returns the fd or -1. */
static int
tcp_listen(int port)
{
  struct sockaddr_in sa;
  int fd, on = 1;

  if ((fd = socket(AF_INET, SOCK_STREAM, 0)) < 0)
    return -1;
  setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &on, sizeof(on));

  memset(&sa, 0, sizeof(sa));
  sa.sin_family = AF_INET;
  sa.sin_addr.s_addr = htonl(INADDR_ANY);
  sa.sin_port = htons((unsigned short)port);

  if (bind(fd, (struct sockaddr *)&sa, sizeof(sa)) < 0 || listen(fd, 1) < 0)
    {
      close(fd);
      return -1;
    }
  set_nonblock(fd);
  return fd;
}

/* Open a connected TCP socket to host:port.  Returns the fd or -1. */
static int
tcp_connect(const char *host, int port)
{
  struct addrinfo hints, *res, *rp;
  char portstr[16];
  int fd = -1;

  memset(&hints, 0, sizeof(hints));
  hints.ai_family = AF_UNSPEC;
  hints.ai_socktype = SOCK_STREAM;
  snprintf(portstr, sizeof(portstr), "%d", port);

  if (getaddrinfo(host, portstr, &hints, &res) != 0)
    return -1;
  for (rp = res; rp; rp = rp->ai_next)
    {
      if ((fd = socket(rp->ai_family, rp->ai_socktype, rp->ai_protocol)) < 0)
        continue;
      if (connect(fd, rp->ai_addr, rp->ai_addrlen) == 0)
        break;
      close(fd);
      fd = -1;
    }
  freeaddrinfo(res);
  if (fd >= 0)
    set_nonblock(fd);
  return fd;
}

/* Open a Unix-domain socket; `do_listen` chooses bind+listen vs connect. */
static int
unix_socket(const char *path, int do_listen)
{
  struct sockaddr_un sa;
  int fd;

  if ((fd = socket(AF_UNIX, SOCK_STREAM, 0)) < 0)
    return -1;

  memset(&sa, 0, sizeof(sa));
  sa.sun_family = AF_UNIX;
  strncpy(sa.sun_path, path, sizeof(sa.sun_path) - 1);

  if (do_listen)
    {
      unlink(path);
      if (bind(fd, (struct sockaddr *)&sa, sizeof(sa)) < 0 || listen(fd, 1) < 0)
        {
          close(fd);
          return -1;
        }
    }
  else if (connect(fd, (struct sockaddr *)&sa, sizeof(sa)) < 0)
    {
      close(fd);
      return -1;
    }
  set_nonblock(fd);
  return fd;
}

/* Parse and open one channel's spec.  Returns >= 0 if usable, -1 otherwise. */
static int
chan_open(sock_chan *c, const char *spec)
{
  c->mode = SOCK_NONE;
  c->listen_fd = -1;
  c->conn_fd = -1;
  c->name[0] = '\0';

  if (!spec)
    return -1;

  if (!strncmp(spec, "listen:", 7))
    {
      int port = atoi(spec + 7);
      if ((c->listen_fd = tcp_listen(port)) < 0)
        return -1;
      c->mode = SOCK_LISTEN;
      snprintf(c->name, sizeof(c->name), "tcp listen :%d", port);
      return c->listen_fd;
    }
  if (!strncmp(spec, "connect:", 8))
    {
      char host[96];
      const char *colon = strrchr(spec + 8, ':');
      int port;
      if (!colon)
        return -1;
      port = atoi(colon + 1);
      snprintf(host, sizeof(host), "%.*s", (int)(colon - (spec + 8)), spec + 8);
      if ((c->conn_fd = tcp_connect(host, port)) < 0)
        return -1;
      c->mode = SOCK_CONNECT;
      snprintf(c->name, sizeof(c->name), "tcp %s:%d", host, port);
      return c->conn_fd;
    }
  if (!strncmp(spec, "unix-listen:", 12))
    {
      if ((c->listen_fd = unix_socket(spec + 12, 1)) < 0)
        return -1;
      c->mode = SOCK_LISTEN;
      snprintf(c->name, sizeof(c->name), "unix listen %s", spec + 12);
      return c->listen_fd;
    }
  if (!strncmp(spec, "unix:", 5))
    {
      if ((c->conn_fd = unix_socket(spec + 5, 0)) < 0)
        return -1;
      c->mode = SOCK_CONNECT;
      snprintf(c->name, sizeof(c->name), "unix %s", spec + 5);
      return c->conn_fd;
    }
  return -1;
}

/*
 *  Return the fd to do I/O on, accepting a pending peer in listen mode.  -1 if
 *  no peer is connected yet (caller treats that as "nothing ready").
 */
static int
chan_io_fd(sock_chan *c)
{
  if (c->conn_fd >= 0)
    return c->conn_fd;

  if (c->mode == SOCK_LISTEN && c->listen_fd >= 0)
    {
      int fd = accept(c->listen_fd, NULL, NULL);
      if (fd >= 0)
        {
          set_nonblock(fd);
          c->conn_fd = fd;
          return fd;
        }
    }
  return -1;
}

/* Drop a peer connection (e.g. on EOF); listen sockets re-accept next time. */
static void
chan_drop_peer(sock_chan *c)
{
  if (c->conn_fd >= 0)
    close(c->conn_fd);
  c->conn_fd = -1;
}

/* ---- hp48_serial_t callbacks ------------------------------------------ */

static int
sock_open(void *user, int channel, const char **name)
{
  /* The sockets were already opened at install time (hp48_serial_socket); here
   * we only report whether this channel ended up usable. */
  sock_chan *c = &chan[channel & 1];

  (void)user;
  if (c->mode == SOCK_NONE)
    return -1;
  if (name)
    *name = c->name;
  return (c->conn_fd >= 0) ? c->conn_fd : c->listen_fd;
}

static int
sock_read(void *user, int channel, unsigned char *buf, int max)
{
  sock_chan *c = &chan[channel & 1];
  int fd = chan_io_fd(c);
  int n;

  (void)user;
  if (fd < 0)
    return 0;

  n = (int)read(fd, buf, (size_t)max);
  if (n == 0)              /* peer closed */
    {
      chan_drop_peer(c);
      return 0;
    }
  if (n < 0)
    return 0;              /* would-block: nothing ready */
  return n;
}

static int
sock_write(void *user, int channel, const unsigned char *buf, int n)
{
  sock_chan *c = &chan[channel & 1];
  int fd = chan_io_fd(c);
  int w;

  (void)user;
  if (fd < 0)
    return 0;              /* no peer yet: drop, as the UART expects */

  w = (int)write(fd, buf, (size_t)n);
  if (w < 0)
    return 0;
  return w;
}

static void
sock_close(void *user, int channel)
{
  sock_chan *c = &chan[channel & 1];
  (void)user;
  chan_drop_peer(c);
  if (c->listen_fd >= 0)
    close(c->listen_fd);
  c->listen_fd = -1;
  c->mode = SOCK_NONE;
}

void
hp48_serial_socket(const char *wire_spec, const char *ir_spec)
{
  hp48_serial_t s;

  memset(chan, 0, sizeof(chan));
  chan[HP48_SERIAL_WIRE].listen_fd = chan[HP48_SERIAL_WIRE].conn_fd = -1;
  chan[HP48_SERIAL_IR].listen_fd   = chan[HP48_SERIAL_IR].conn_fd   = -1;

  /* Open the sockets now so failures are visible at install time and the
   * connection names are ready for the UI. */
  chan_open(&chan[HP48_SERIAL_WIRE], wire_spec);
  chan_open(&chan[HP48_SERIAL_IR], ir_spec);

  memset(&s, 0, sizeof(s));
  s.open  = sock_open;
  s.read  = sock_read;
  s.write = sock_write;
  s.close = sock_close;
  /* no configure(): baud is meaningless on a socket */
  hp48_set_serial(&s);
}
