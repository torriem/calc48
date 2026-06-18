/*
 *  RPL user-stack read API (Phase R of docs/stack_io_plan.md).
 *
 *  Reads objects off the calculator's RPL user stack -- a way to get data out
 *  of the emulator besides the plug-in ports.  Read-only and side-effect free
 *  (it saves/restores the RAM bank mapping it forces).  Built only in a GX-only
 *  configuration (CMake HP48_GX_ONLY); the stack and object-sizing logic here is
 *  hard-coded for the HP 48 G/GX.  On a non-GX instance the entry points fail
 *  safe.
 *
 *  Much of the object traversal is ported from droid48's binio.c
 *  (RPL_ObjectSize); the nibble accessors (load_addr / read_nibble) and the
 *  prologue constants (rpl.h) are libcalc48's own.  See docs/stack_io_plan.md.
 */

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

#include "hp48.h"        /* word_20, saturn / opt_gx bridge, load_addr */
#include "hp48_emu.h"    /* read_nibble */
#include "rpl.h"         /* prologue constants, decode_rpl_obj_2 */
#include "hp48_rpl.h"

/* GX system-RAM pointers to the data stack (same values debugger.c uses). */
#define DSKTOP_GX  0x806f8
#define DSKBOT_GX  0x806fd

/* Standard GX RAM bank mapping; mem_cntl[1] selects which 256K window is RAM. */
#define GX_RAM_BASE  0x80000
#define GX_RAM_MASK  0xc0000

/* Guard against runaway recursion on corrupt data. */
#define RPL_MAX_DEPTH  256

/*
 *  Force the known GX RAM mapping so absolute system addresses resolve through
 *  read_nibble regardless of the calc's current bank state, then restore it.
 *  (Proven approach borrowed from debugger.c's do_stack; harmless because we
 *  always restore.)
 */
static void
map_gx_ram(word_20 *save_base, word_20 *save_mask)
{
  *save_base = saturn.mem_cntl[1].config[0];
  *save_mask = saturn.mem_cntl[1].config[1];
  saturn.mem_cntl[1].config[0] = GX_RAM_BASE;
  saturn.mem_cntl[1].config[1] = GX_RAM_MASK;
}

static void
unmap_gx_ram(word_20 save_base, word_20 save_mask)
{
  saturn.mem_cntl[1].config[0] = save_base;
  saturn.mem_cntl[1].config[1] = save_mask;
}

/* Read `n` (<= 5) nibbles at `addr` as a packed value (LSB nibble first). */
static word_20
read_packed(word_20 addr, int n)
{
  word_20 v = 0;
  load_addr(&v, addr, n);
  return v;
}

/*
 *  Total nibble length of the object at `addr` (prologue + body), recursing into
 *  composites.  Ported from droid48 RPL_ObjectSize; returns < 0 if the recursion
 *  guard trips (corrupt data), 0 for SEMI, 5 for an unknown prologue.
 */
static int
obj_size(word_20 addr, int depth)
{
  word_20 prolog, n, l = 0;

  if (depth > RPL_MAX_DEPTH)
    return -1;

  prolog = read_packed(addr, 5);
  switch (prolog)
    {
      case DOBINT:  return 10;   /* System Binary    */
      case DOREAL:  return 21;   /* Real             */
      case DOEREL:  return 26;   /* Long Real        */
      case DOCMP:   return 37;   /* Complex          */
      case DOECMP:  return 47;   /* Long Complex     */
      case DOCHAR:  return 7;    /* Character        */
      case DOACPTR: return 15;   /* Extended Pointer */
      case DOROMP:  return 11;   /* XLIB Name        */

      case DOLIST:               /* composites: walk elements until SEMI */
      case DOSYMB:
      case DOEXT:
      case DOCOL:
        n = 5;
        for (;;)
          {
            int e;
            l += n;
            addr += n;
            e = obj_size(addr, depth + 1);
            if (e < 0)
              return -1;
            if (e == 0)
              break;
            n = (word_20)e;
          }
        l += 5;                  /* the SEMI itself */
        return (int)l;

      case SEMI:    return 0;

      case DOIDNT:               /* global/local name: prologue + len + chars, */
      case DOLAM:                /* no trailing object (unlike droid48's binio) */
        return (int)(7 + read_packed(addr + 5, 2) * 2);

      case DOTAG:                /* tag chars, then the tagged object */
        {
          int rest;
          n = 7 + read_packed(addr + 5, 2) * 2;
          rest = obj_size(addr + n, depth + 1);
          if (rest < 0)
            return -1;
          return (int)n + rest;
        }

      case DORRP:                /* Directory */
        {
          int rest;
          n = read_packed(addr + 8, 5);
          if (n == 0)
            return 13;           /* empty directory */
          l = 8 + n;
          n = read_packed(addr + l, 2) * 2 + 4;
          l += n;
          rest = obj_size(addr + l, depth + 1);
          if (rest < 0)
            return -1;
          return (int)l + rest;
        }

      case DOARRY:               /* length-prefixed body (5-nibble length) */
      case DOLNKARRY:
      case DOCSTR:
      case DOHSTR:
      case DOGROB:
      case DOLIB:
      case DOBAK:
      case DOEXT0:
      case DOEXT2:
      case DOEXT3:
      case DOEXT4:
      case DOCODE:
        return (int)(5 + read_packed(addr + 5, 5));

      default:
        return 5;                /* unknown: at least the prologue */
    }
}

/* ---- public API ------------------------------------------------------- */

int
hp48_stack_depth(void)
{
  word_20 base, mask, sp = 0, end = 0;
  int n;

  if (!opt_gx)
    return 0;

  map_gx_ram(&base, &mask);
  load_addr(&sp, DSKTOP_GX, 5);
  load_addr(&end, DSKBOT_GX, 5);
  unmap_gx_ram(base, mask);

  if (end <= sp)
    return 0;
  n = (int)((end - sp) / 5) - 1;   /* end never matches sp */
  return (n < 0) ? 0 : n;
}

uint32_t
hp48_stack_addr(int level)
{
  word_20 base, mask, sp = 0, end = 0, ent = 0;
  int depth;

  if (!opt_gx || level < 1)
    return 0;

  map_gx_ram(&base, &mask);
  load_addr(&sp, DSKTOP_GX, 5);
  load_addr(&end, DSKBOT_GX, 5);
  if (end > sp)
    {
      depth = (int)((end - sp) / 5) - 1;
      if (level <= depth)                       /* level 1 = top (at sp) */
        load_addr(&ent, sp + 5 * (level - 1), 5);
    }
  unmap_gx_ram(base, mask);

  return (uint32_t)ent;
}

uint32_t
hp48_object_prolog(uint32_t addr)
{
  word_20 base, mask, prolog;

  if (!opt_gx)
    return 0;

  map_gx_ram(&base, &mask);
  prolog = read_packed((word_20)addr, 5);
  unmap_gx_ram(base, mask);
  return (uint32_t)prolog;
}

int
hp48_object_size(uint32_t addr)
{
  word_20 base, mask;
  int sz;

  if (!opt_gx)
    return -1;

  map_gx_ram(&base, &mask);
  sz = obj_size((word_20)addr, 0);
  unmap_gx_ram(base, mask);
  return sz;
}

int
hp48_read_object(uint32_t addr, unsigned char *out, int max)
{
  word_20 base, mask;
  int sz, i, ncopy;

  if (!opt_gx || !out)
    return -1;

  map_gx_ram(&base, &mask);
  sz = obj_size((word_20)addr, 0);
  if (sz > 0)
    {
      ncopy = (sz < max) ? sz : max;
      for (i = 0; i < ncopy; i++)
        out[i] = (unsigned char)(read_nibble((word_20)addr + i) & 0x0f);
    }
  unmap_gx_ram(base, mask);
  return sz;
}

int
hp48_stack_describe(int level, char *out, int max)
{
  uint32_t addr;
  word_20 base, mask;
  char *typ, *dat;
  int len;

  if (!out || max <= 0)
    return -1;

  addr = hp48_stack_addr(level);    /* maps/unmaps internally; 0 if bad level */
  if (addr == 0)
    return -1;

  typ = (char *)malloc(256);
  dat = (char *)malloc(65536);
  if (!typ || !dat)
    {
      free(typ);
      free(dat);
      return -1;
    }

  map_gx_ram(&base, &mask);
  decode_rpl_obj_2((word_20)addr, typ, dat);
  unmap_gx_ram(base, mask);

  len = snprintf(out, (size_t)max, "[%s] %s", typ, dat);
  free(typ);
  free(dat);
  return (len < 0) ? -1 : len;
}
