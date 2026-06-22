/*
 *  This file is part of x48, an emulator of the HP-48sx Calculator.
 *  Copyright (C) 1994  Eddie C. Dost  (ecd@dressler.de)
 *
 *  This program is free software; you can redistribute it and/or modify
 *  it under the terms of the GNU General Public License as published by
 *  the Free Software Foundation; either version 2 of the License, or
 *  (at your option) any later version.
 *
 *  This program is distributed in the hope that it will be useful,
 *  but WITHOUT ANY WARRANTY; without even the implied warranty of
 *  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 *  GNU General Public License for more details.
 *
 *  You should have received a copy of the GNU General Public License
 *  along with this program; if not, write to the Free Software
 *  Foundation, Inc., 675 Mass Ave, Cambridge, MA 02139, USA.
 */

/*
 *  UART emulation: the portable half of the serial port (Phase 6 of
 *  REFACTOR_PLAN.md).
 *
 *  This file emulates the HP 48 UART -- the receive/transmit buffers, the
 *  control/status bits and the interrupts -- and is free of host syscalls.  The
 *  actual transport (a PTY, a real tty, a socket, an in-memory pipe, ...) lives
 *  behind the hp48_serial_t callbacks (hp48_serial.h), which the host installs
 *  with hp48_set_serial().  With no transport installed both channels are simply
 *  "unplugged" and the UART behaves as if nothing is connected.
 */

#include "global.h"

#include <stddef.h>

#include "hp48.h"
#include "hp48_serial.h"
#include "device.h"
#include "hp48_emu.h"

/*
 *  Connection names reported by the provider's open() (provider-owned, stable
 *  strings).  Only used to drive the UI's show_connections callback.
 */
static const char *wire_name = NULL;
static const char *ir_name   = NULL;

/* #define DEBUG_SERIAL */

void
update_connection_display(void)
{
  if (cpu->ui.show_connections)
    cpu->ui.show_connections(cpu->ui.user,
                             (wire_fd == -1) ? NULL : wire_name,
                             (ir_fd   == -1) ? NULL : ir_name);
}

/*
 *  Open both channels through the installed transport.  wire_fd / ir_fd are the
 *  core's per-channel "connected" flags (>= 0 connected, -1 unplugged); the real
 *  fd/handle, if any, is the provider's business.
 */
int
serial_init(void)
{
  wire_fd = -1;
  ir_fd   = -1;
  wire_name = NULL;
  ir_name   = NULL;

  if (cpu->serial.open)
    {
      wire_fd = cpu->serial.open(cpu->serial.user, HP48_SERIAL_WIRE, &wire_name);
      ir_fd   = cpu->serial.open(cpu->serial.user, HP48_SERIAL_IR,   &ir_name);
    }

  update_connection_display();
  return 1;
}

void
serial_baud(int baud)
{
  if (!cpu->serial.configure)
    return;

  if (ir_fd != -1)
    cpu->serial.configure(cpu->serial.user, HP48_SERIAL_IR, baud);
  if (wire_fd != -1)
    cpu->serial.configure(cpu->serial.user, HP48_SERIAL_WIRE, baud);
}

void
transmit_char(void)
{
  int channel = (saturn.ir_ctrl & 0x04) ? HP48_SERIAL_IR : HP48_SERIAL_WIRE;
  int connected = (channel == HP48_SERIAL_IR) ? ir_fd : wire_fd;

#ifdef DEBUG_SERIAL
  if (isprint(saturn.tbr))
    fprintf(stderr, "-> \'%c\'\n", saturn.tbr);
  else
    fprintf(stderr, "-> %x\n", saturn.tbr);
#endif

  /*
   *  Push the byte if the channel is connected.  Whether it is connected or the
   *  write succeeds, the calculator expects the transmit to "complete": clear
   *  the busy bit and raise the transmit interrupt (a dropped byte on an
   *  unplugged / full line matches the historical behaviour).
   */
  if (connected != -1 && cpu->serial.write)
    cpu->serial.write(cpu->serial.user, channel, &saturn.tbr, 1);

  saturn.tcs &= 0x0e;
  if (saturn.io_ctrl & 0x04)
    do_interupt();
}

#define NR_BUFFER 256

void
receive_char(void)
{
  /*
   *  The transport hands us bytes in bursts; we dole them out to the UART one at
   *  a time across calls.  (Static buffer, as in the original; a single serial
   *  connection at a time, which is all the desktop providers offer.)
   */
  static unsigned char buf[NR_BUFFER];
  static int nrd = 0, bp = 0;

  int channel = (saturn.ir_ctrl & 0x04) ? HP48_SERIAL_IR : HP48_SERIAL_WIRE;
  int connected = (channel == HP48_SERIAL_IR) ? ir_fd : wire_fd;

  rece_instr = 0;

  if (connected == -1)
    return;

  /* receive buffer register still full -- the calc hasn't read it yet */
  if (saturn.rcs & 0x01)
    return;

  if (nrd == 0)
    {
      if (!cpu->serial.read)
        return;
      nrd = cpu->serial.read(cpu->serial.user, channel, buf, NR_BUFFER);
      if (nrd <= 0)
        {
          nrd = 0;
          return;
        }
      bp = 0;
    }

  /* receiver not enabled -- discard what we pulled */
  if (!(saturn.io_ctrl & 0x08))
    {
      nrd = 0;
      return;
    }

  saturn.rbr = buf[bp++];
  nrd--;
  saturn.rcs |= 0x01;
  if (saturn.io_ctrl & 0x02)
    do_interupt();
}
