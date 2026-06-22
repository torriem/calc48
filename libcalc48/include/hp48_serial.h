/*
 *  Serial transport interface for the emulator core (Phase 6 of
 *  REFACTOR_PLAN.md).
 *
 *  The HP 48 UART itself -- the RCS/TCS/RBR/TBR registers, the status bits and
 *  the interrupts -- is emulated in the core (memory.c + serial.c) and is fully
 *  portable.  The *transport* (the actual "wire": a host pseudo-terminal, a real
 *  serial device, a socket, an in-memory pipe, ...) is NOT portable, so the core
 *  reaches it only through these callbacks, which the host installs.
 *
 *  A desktop front end can install a bundled provider (hp48_serial_pty.c /
 *  hp48_serial_socket.c); an embedder on a restricted platform (Android, iOS,
 *  Qt, a test harness) supplies its own hp48_serial_t and the core makes no
 *  serial-related host syscalls at all.
 *
 *  This header pulls in no SDL and no stdio types.
 */

#ifndef _HP48_SERIAL_H
#define _HP48_SERIAL_H 1

/*
 *  The two independent UART channels.  The calculator selects which one is live
 *  via the IR-control register (saturn.ir_ctrl & 0x04); the core passes the
 *  resulting channel to every callback so one provider can back both.
 */
#define HP48_SERIAL_WIRE	0	/* the "wire" port (desktop: a PTY)     */
#define HP48_SERIAL_IR		1	/* the IR port  (desktop: a tty device) */

/*
 *  Transport callbacks supplied by the host.  Any callback may be NULL; the
 *  core checks before calling, so an instance with no serial simply has both
 *  channels "unplugged" (the UART reports no device, exactly as a -1 fd did).
 *  `user` is an opaque pointer handed back to every callback.
 */
typedef struct hp48_serial_t {
  void *user;

  /*
   *  Open `channel`.  Return >= 0 if it is connected, < 0 if unplugged.  If a
   *  human-readable name exists (a PTY slave path, a device node, a socket
   *  address) store a provider-owned, stable pointer in *name for the UI to
   *  display; otherwise leave *name alone or set it to NULL.  May be NULL, in
   *  which case the channel is treated as unplugged.
   */
  int  (*open)(void *user, int channel, const char **name);

  /*
   *  Apply the calculator's baud-rate code (0..7) to `channel`.  Optional;
   *  meaningful for a real tty/PTY, a no-op for most stream transports.
   */
  void (*configure)(void *user, int channel, int baud);

  /*
   *  NON-blocking read of up to `max` bytes from `channel` into `buf`.  Return
   *  the number read (0 if nothing is ready right now), or < 0 on error.  Must
   *  never block: the core polls this from its run loop.
   */
  int  (*read)(void *user, int channel, unsigned char *buf, int max);

  /*
   *  Write `n` bytes of `buf` to `channel`.  Return the number written, or < 0
   *  on error / would-block.  The core sends one byte at a time.
   */
  int  (*write)(void *user, int channel, const unsigned char *buf, int n);

  /* Close `channel` and release its resources.  Optional. */
  void (*close)(void *user, int channel);
} hp48_serial_t;

/* Install the host's serial transport on the active emulator instance. */
extern void hp48_set_serial(const hp48_serial_t *serial);

/* ---- bundled providers (optional; built only with HP48_WITH_SERIAL) -------
 *
 *  Desktop conveniences that back the channels with host facilities.  Not part
 *  of a minimal/embedded build, where the host installs its own hp48_serial_t.
 */

/*
 *  Pseudo-terminal / device provider.  The wire channel is a freshly allocated
 *  PTY (its slave path is reported as the connection name, for a host program
 *  such as Kermit to attach to); the IR channel is the real tty device at
 *  `ir_device` (NULL = IR unplugged).  `verbose` prints the connection names.
 */
extern void hp48_serial_pty(int use_wire, const char *ir_device, int verbose);

/*
 *  Socket provider.  Each channel is given a spec string (NULL = unplugged):
 *    "listen:PORT"          -- TCP, wait for a connection on PORT
 *    "connect:HOST:PORT"    -- TCP, connect to HOST:PORT
 *    "unix:PATH"            -- connect to a Unix-domain socket at PATH
 *    "unix-listen:PATH"     -- create/listen on a Unix-domain socket at PATH
 */
extern void hp48_serial_socket(const char *wire_spec, const char *ir_spec);

#endif /* !_HP48_SERIAL_H */
