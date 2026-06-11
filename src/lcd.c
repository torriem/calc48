/*
	SDL port of x48
	Copyright (C) 2011-2012 Daniel Roggen
	Revision 1.0
*/
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

/* $Log: lcd.c,v $
 * Revision 1.13  1995/01/11  18:20:01  ecd
 * major update to support HP48 G/GX
 *
 * Revision 1.12  1994/12/08  22:14:50  ecd
 * fixed bug with XShmPutImage causing errors in init_display
 *
 * Revision 1.11  1994/12/07  20:20:50  ecd
 * added support for icon colors
 *
 * Revision 1.10  1994/11/28  02:00:51  ecd
 * added support for colors on icon
 *
 * Revision 1.9  1994/11/02  14:44:28  ecd
 * minor fixes
 *
 * Revision 1.8  1994/10/09  20:32:02  ecd
 * implemented bit offset stuff.
 *
 * Revision 1.7  1994/10/06  16:30:05  ecd
 * added Shared Memory stuff
 *
 * Revision 1.6  1994/10/05  08:36:44  ecd
 * pixmaps for nibble updates
 *
 * Revision 1.5  1994/09/30  12:37:09  ecd
 * new display code makes x48 a lot faster
 *
 * Revision 1.4  1994/09/18  15:29:22  ecd
 * turned off unused rcsid message
 *
 * Revision 1.3  1994/09/13  16:57:00  ecd
 * changed to plain X11
 *
 * Revision 1.2  1994/08/31  18:23:21  ecd
 * changed display initialization.
 *
 * Revision 1.1  1994/08/26  11:09:02  ecd
 * Initial revision
 *
 * $Id: lcd.c,v 1.13 1995/01/11 18:20:01 ecd Exp ecd $
 */


#include "global.h"

#include <stdio.h>
#include <unistd.h>
#include <string.h>
#ifdef SUNOS
#include <memory.h>
#endif


#include "hp48.h"
#include "hp48_emu.h"
#include "x48_sdl.h"
#include "annunc.h"
#include "device.h"

/* display, disp_buf, lcd_buffer, last_annunc_state are now hp48_t members
 * (see hp48_state.h). */

ann_struct_t ann_tbl[] = {
  { ANN_LEFT, 16, 4, ann_left_width, ann_left_height, ann_left_bits },
  { ANN_RIGHT, 61, 4, ann_right_width, ann_right_height, ann_right_bits },
  { ANN_ALPHA, 106, 4, ann_alpha_width, ann_alpha_height, ann_alpha_bits },
  { ANN_BATTERY, 151, 4, ann_battery_width, ann_battery_height,
                         ann_battery_bits },
  { ANN_BUSY, 196, 4, ann_busy_width, ann_busy_height, ann_busy_bits },
  { ANN_IO, 241, 4, ann_io_width, ann_io_height, ann_io_bits },
  { 0 }
};




void
#ifdef __FunctionProto__
init_display(void)
#else
init_display()
#endif
{
  cpu->display.on = (int)(saturn.disp_io & 0x8) >> 3;

  cpu->display.disp_start = (saturn.disp_addr & 0xffffe);
  cpu->display.offset = (saturn.disp_io & 0x7);
  disp.offset = 2 * cpu->display.offset;

  cpu->display.lines = (saturn.line_count & 0x3f);
  if (cpu->display.lines == 0)
    cpu->display.lines = 63;
  disp.lines = 2 * cpu->display.lines;
  if (disp.lines < 110)
    disp.lines = 110;

  if (cpu->display.offset > 3)
    cpu->display.nibs_per_line = (NIBBLES_PER_ROW+saturn.line_offset+2) & 0xfff;
  else
    cpu->display.nibs_per_line = (NIBBLES_PER_ROW+saturn.line_offset) & 0xfff;

  cpu->display.disp_end = cpu->display.disp_start +
	             (cpu->display.nibs_per_line * (cpu->display.lines + 1));

  cpu->display.menu_start = saturn.menu_addr;
  cpu->display.menu_end = saturn.menu_addr + 0x110;

  cpu->display.contrast = saturn.contrast_ctrl;
  cpu->display.contrast |= ((saturn.disp_test & 0x1) << 4);

  cpu->display.annunc = saturn.annunc;

  memset(disp_buf, 0xf0, sizeof(disp_buf));
  memset(lcd_buffer, 0xf0, sizeof(lcd_buffer));

}

static inline void
#ifdef __FunctionProto__
draw_nibble(int c, int r, int val)
#else
draw_nibble(c, r, val)
int c;
int r;
int val;
#endif
{
  int x, y;
  

  if (val != lcd_buffer[r][c]) {
    lcd_buffer[r][c] = val;
  }
	///////////////////////////////////////////////
  	// SDL PORT
  	///////////////////////////////////////////////
	x = (c * 4);					// x: start in pixels
	if (r <= cpu->display.lines)
		x -= disp.offset;			// Correct the pixels with display offset
	y = r;							// y: start in pixels
  	SDLDrawNibble(x,y,val);

}

static inline void
#ifdef __FunctionProto__
draw_row(long addr, int row)
#else
draw_row(addr, row)
long addr;
int row;
#endif
{
  int i, v;
  int line_length;
  

  line_length = NIBBLES_PER_ROW;
  if ((cpu->display.offset > 3) && (row <= cpu->display.lines))
    line_length += 2;
  for (i = 0; i < line_length; i++) {
    v = read_nibble(addr + i);
    if (v != disp_buf[row][i]) {
      disp_buf[row][i] = v;
      draw_nibble(i, row, v);
    }
  }
}

void
#ifdef __FunctionProto__
update_display(void)
#else
update_display()
#endif
{
  int i, j;
  long addr;
  static int old_offset = -1;
  static int old_lines = -1;


  	if (!disp.mapped)
	{
      //refresh_icon();
		return;
   }
  if (cpu->display.on) {
    addr = cpu->display.disp_start;
      if (cpu->display.offset != old_offset) {
        memset(disp_buf, 0xf0,
               (size_t)((cpu->display.lines+1) * NIBS_PER_BUFFER_ROW));
        memset(lcd_buffer, 0xf0,
               (size_t)((cpu->display.lines+1) * NIBS_PER_BUFFER_ROW));
        old_offset = cpu->display.offset;
      }
      if (cpu->display.lines != old_lines) {
        memset(&disp_buf[56][0], 0xf0, (size_t)(8 * NIBS_PER_BUFFER_ROW));
        memset(&lcd_buffer[56][0], 0xf0, (size_t)(8 * NIBS_PER_BUFFER_ROW));
        old_lines = cpu->display.lines;
      }
      for (i = 0; i <= cpu->display.lines; i++) {
        draw_row(addr, i);
        addr += cpu->display.nibs_per_line;
      }
    if (i < DISP_ROWS) {
      addr = cpu->display.menu_start;

        for (; i < DISP_ROWS; i++) {
          draw_row(addr, i);
          addr += NIBBLES_PER_ROW;
        }
    }
  } else {
      memset(disp_buf, 0xf0, sizeof(disp_buf));
      for (i = 0; i < 64; i++) {
        for (j = 0; j < NIBBLES_PER_ROW; j++) {
          draw_nibble(j, i, 0x00);
        }
      }
  }
  
}

void
#ifdef __FunctionProto__
redraw_display(void)
#else
redraw_display()
#endif
{
  memset(disp_buf, 0, sizeof(disp_buf));
  memset(lcd_buffer, 0, sizeof(lcd_buffer));
  update_display();
}

void
#ifdef __FunctionProto__
disp_draw_nibble(word_20 addr, word_4 val)
#else
disp_draw_nibble(addr, val)
word_20 addr;
word_4 val;
#endif
{
  long offset;
  int x, y;
  
	
  offset = (addr - cpu->display.disp_start);
  x = offset % cpu->display.nibs_per_line;
  if (x < 0 || x > 35)
    return;
  if (cpu->display.nibs_per_line != 0) {
    y = offset / cpu->display.nibs_per_line;
    if (y < 0 || y > 63)
      return;
      if (val != disp_buf[y][x]) {
        disp_buf[y][x] = val;
        draw_nibble(x, y, val);
      }
  } else {
      for (y = 0; y < cpu->display.lines; y++) {
        if (val != disp_buf[y][x]) {
          disp_buf[y][x] = val;
          draw_nibble(x, y, val);
        }
      }
  }
}

void
#ifdef __FunctionProto__
menu_draw_nibble(word_20 addr, word_4 val)
#else
menu_draw_nibble(addr, val)
word_20 addr;
word_4 val;
#endif
{
  long offset;
  int x, y;

  offset = (addr - cpu->display.menu_start);
    x = offset % NIBBLES_PER_ROW;
    y = cpu->display.lines + (offset / NIBBLES_PER_ROW) + 1;
    if (val != disp_buf[y][x]) {
      disp_buf[y][x] = val;
      draw_nibble(x, y, val);
    }
}



void
#ifdef __FunctionProto__
draw_annunc(void)
#else
draw_annunc()
#endif
{
  int val;
  int i;

  val = cpu->display.annunc;

  if (val == last_annunc_state)
    return;
  last_annunc_state = val;

  ///////////////////////////////////////////////
  // SDL PORT
  ///////////////////////////////////////////////
	char sdl_annuncstate[6];
	for (i = 0; ann_tbl[i].bit; i++)
	{
		if ((ann_tbl[i].bit & val) == ann_tbl[i].bit)
			sdl_annuncstate[i] = 1;
		else
			sdl_annuncstate[i] = 0;
	}      	
  SDLDrawAnnunc(sdl_annuncstate);
}

void
#ifdef __FunctionProto__
redraw_annunc(void)
#else
redraw_annunc()
#endif
{
  last_annunc_state = -1;
  draw_annunc();
}


