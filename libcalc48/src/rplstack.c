/*
 *  RPL user-stack read/write API (Phases R + W2 of docs/stack_io_plan.md).
 *
 *  Reads objects off, and pushes objects onto, the calculator's RPL user stack
 *  -- a way to move data in/out of the emulator besides the plug-in ports.  The
 *  read side is side-effect free; both save/restore the RAM bank mapping they
 *  force.  Call at a safe point (the calc idle, between hp48_run_slice() calls).
 *
 *  SX and GX are both supported and selected at runtime from `opt_gx` (set from
 *  the loaded ROM); HP49 (opt_gx >= 2) is not supported and the entry points
 *  fail safe.  Built behind the HP48_WITH_STACK_IO option.
 *
 *  Much of the traversal/allocation is ported from droid48's binio.c
 *  (RPL_ObjectSize / RPL_CreateTemp / RPL_Push); the nibble accessors
 *  (load_addr / read_nibble / write_nibbles) and the prologue + manager-pointer
 *  constants (rpl.h) are libcalc48's own.  See docs/stack_io_plan.md.
 */

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

#include "hp48.h"        /* word_20, saturn / opt_gx bridge, load_addr */
#include "hp48_emu.h"    /* read_nibble, write_nibbles, load_address, store_n */
#include "rpl.h"         /* prologue + manager-pointer constants, decode */
#include "hp48_rpl.h"

/* Guard against runaway recursion on corrupt data. */
#define RPL_MAX_DEPTH  256

/*
 *  Per-model memory map: the data-stack and memory-manager pointer addresses,
 *  plus the RAM bank window mem_cntl[1] must select for those absolute system
 *  addresses to resolve.  Selected at runtime by opt_gx.
 */
typedef struct {
  word_20 dsktop, dskbot, tempob, temptop, rsktop, avmem;
  word_20 ram_base, ram_mask;
} rpl_map_t;

static const rpl_map_t MAP_SX = {
  DSKTOP_SX, DSKBOT_SX, TEMPOB_SX, TEMPTOP_SX, RSKTOP_SX, AVMEM_SX,
  0x70000, 0xf0000
};
static const rpl_map_t MAP_GX = {
  DSKTOP_GX, DSKBOT_GX, TEMPOB_GX, TEMPTOP_GX, RSKTOP_GX, AVMEM_GX,
  0x80000, 0xc0000
};

/* The map for the active instance, or NULL if the model is unsupported (HP49). */
static const rpl_map_t *
rpl_map(void)
{
  if (opt_gx == 1)
    return &MAP_GX;
  if (opt_gx == 0)
    return &MAP_SX;
  return NULL;                 /* opt_gx >= 2: HP49, not supported */
}

/*
 *  Force the model's RAM bank mapping so absolute system addresses resolve
 *  through read_nibble/write_nibble regardless of the calc's current bank state,
 *  then restore it.  (Proven approach borrowed from debugger.c's do_stack;
 *  harmless because we always restore.)
 */
static void
map_ram(const rpl_map_t *m, word_20 *save_base, word_20 *save_mask)
{
  *save_base = saturn.mem_cntl[1].config[0];
  *save_mask = saturn.mem_cntl[1].config[1];
  saturn.mem_cntl[1].config[0] = m->ram_base;
  saturn.mem_cntl[1].config[1] = m->ram_mask;
}

static void
unmap_ram(word_20 save_base, word_20 save_mask)
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
  const rpl_map_t *m = rpl_map();
  word_20 base, mask, sp = 0, end = 0;
  int n;

  if (!m)
    return 0;

  map_ram(m, &base, &mask);
  load_addr(&sp, m->dsktop, 5);
  load_addr(&end, m->dskbot, 5);
  unmap_ram(base, mask);

  if (end <= sp)
    return 0;
  n = (int)((end - sp) / 5) - 1;   /* end never matches sp */
  return (n < 0) ? 0 : n;
}

uint32_t
hp48_stack_addr(int level)
{
  const rpl_map_t *m = rpl_map();
  word_20 base, mask, sp = 0, end = 0, ent = 0;
  int depth;

  if (!m || level < 1)
    return 0;

  map_ram(m, &base, &mask);
  load_addr(&sp, m->dsktop, 5);
  load_addr(&end, m->dskbot, 5);
  if (end > sp)
    {
      depth = (int)((end - sp) / 5) - 1;
      if (level <= depth)                       /* level 1 = top (at sp) */
        load_addr(&ent, sp + 5 * (level - 1), 5);
    }
  unmap_ram(base, mask);

  return (uint32_t)ent;
}

uint32_t
hp48_object_prolog(uint32_t addr)
{
  const rpl_map_t *m = rpl_map();
  word_20 base, mask, prolog;

  if (!m)
    return 0;

  map_ram(m, &base, &mask);
  prolog = read_packed((word_20)addr, 5);
  unmap_ram(base, mask);
  return (uint32_t)prolog;
}

int
hp48_object_size(uint32_t addr)
{
  const rpl_map_t *m = rpl_map();
  word_20 base, mask;
  int sz;

  if (!m)
    return -1;

  map_ram(m, &base, &mask);
  sz = obj_size((word_20)addr, 0);
  unmap_ram(base, mask);
  return sz;
}

int
hp48_read_object(uint32_t addr, unsigned char *out, int max)
{
  const rpl_map_t *m = rpl_map();
  word_20 base, mask;
  int sz, i, ncopy;

  if (!m || !out)
    return -1;

  map_ram(m, &base, &mask);
  sz = obj_size((word_20)addr, 0);
  if (sz > 0)
    {
      ncopy = (sz < max) ? sz : max;
      for (i = 0; i < ncopy; i++)
        out[i] = (unsigned char)(read_nibble((word_20)addr + i) & 0x0f);
    }
  unmap_ram(base, mask);
  return sz;
}

int
hp48_stack_describe(int level, char *out, int max)
{
  const rpl_map_t *m = rpl_map();
  uint32_t addr;
  word_20 base, mask;
  char *typ, *dat;
  int len;

  if (!m || !out || max <= 0)
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

  map_ram(m, &base, &mask);
  decode_rpl_obj_2((word_20)addr, typ, dat);
  unmap_ram(base, mask);

  len = snprintf(out, (size_t)max, "[%s] %s", typ, dat);
  free(typ);
  free(dat);
  return (len < 0) ? -1 : len;
}

/* ---- WRITE / push (ported from droid48 binio.c) ----------------------- */

/*
 *  Allocate `nibbles` of temporary-object space and return the base address of
 *  the new (uninitialised) object, or 0 on failure.  Ported from RPL_CreateTemp:
 *  the chunk is carved at the top of TEMPOB and the return stack is slid up to
 *  make room, so no existing object moves and no stack pointers need fixing.
 *  Caller must already hold the forced RAM mapping for `m`.
 */
static word_20
rpl_alloc_temp(const rpl_map_t *m, int nibbles)
{
  word_20 a, b, c, total, blocklen;
  unsigned char *buf = NULL;

  if (nibbles <= 0)
    return 0;
  total = (word_20)nibbles + 6;          /* + 5-nibble length field + 1 marker */

  a = read_packed(m->temptop, 5);        /* end of top temp object        */
  b = read_packed(m->rsktop, 5);         /* end of return stack           */
  c = read_packed(m->dsktop, 5);         /* top of data stack             */

  if (b < a || c < b)                    /* pointers must be ordered      */
    return 0;
  if (b + total > c)                     /* not enough contiguous free RAM */
    return 0;

  /* Grab the relocation scratch buffer *before* mutating any pointers, so a
   * malloc failure leaves the calculator state untouched. */
  blocklen = b - a;
  if (blocklen > 0)
    {
      buf = (unsigned char *)malloc((size_t)blocklen);
      if (!buf)
        return 0;
    }

  write_nibbles(m->temptop, (long)(a + total), 5);
  write_nibbles(m->rsktop,  (long)(b + total), 5);
  write_nibbles(m->avmem,   (long)((c - b - total) / 5), 5);

  /* Slide the return stack [a,b) up by `total`.  Ranges overlap, so copy via
   * the snapshot buffer rather than a forward store_n. */
  if (blocklen > 0)
    {
      load_address(buf, a, (int)blocklen);
      store_n(a + total, buf, (int)blocklen);
      free(buf);
    }

  write_nibbles(a + total - 5, (long)total, 5);   /* object length field */
  return a + 1;                                    /* base = prologue addr */
}

/* Push a pointer to an object onto stack level 1 (ported from RPL_Push). */
static int
rpl_push(const rpl_map_t *m, word_20 addr)
{
  word_20 avmem, stkp;

  avmem = read_packed(m->avmem, 5);
  if (avmem == 0)
    return -1;                           /* no room for another stack slot */
  write_nibbles(m->avmem, (long)(avmem - 1), 5);

  stkp = read_packed(m->dsktop, 5);
  stkp -= 5;
  write_nibbles(stkp, (long)addr, 5);    /* new level-1 pointer */
  write_nibbles(m->dsktop, (long)stkp, 5);
  return 0;
}

int
hp48_stack_push_object(const unsigned char *obj, int n)
{
  const rpl_map_t *m = rpl_map();
  word_20 base, mask, addr;
  int rc;

  if (!m || !obj || n < 5)               /* need at least a 5-nibble prologue */
    return -1;

  map_ram(m, &base, &mask);

  addr = rpl_alloc_temp(m, n);
  if (addr == 0)
    {
      unmap_ram(base, mask);
      return -1;
    }
  store_n(addr, (unsigned char *)obj, n);
  rc = rpl_push(m, addr);

  unmap_ram(base, mask);
  return rc;
}
