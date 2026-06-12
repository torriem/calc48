/*
 *  Headless embedding smoke test for libcalc48.
 *
 *  Links against ONLY the emulator core (libcalc48) -- no SDL, no UI -- and
 *  exercises the Phase 4 public API: create an instance, read the LCD buffer,
 *  drive the key matrix, and destroy.  It does not load a ROM, so it never
 *  runs instructions; its job is to prove the core is a self-contained,
 *  toolkit-independent library that an embedder (Qt, Python, a test harness)
 *  can link and call without SDL.
 *
 *  Build via the CMake target `hp48_ffi_smoke`, or by hand:
 *      gcc -I src -o ffi_smoke examples/ffi_smoke.c build/libcalc48.a
 */

#include <stdio.h>

#include "hp48.h"

int main(void)
{
    hp48_t *h = hp48_create();
    if (!h) {
        fprintf(stderr, "hp48_create failed\n");
        return 1;
    }

    int rows = -1, stride = -1;
    const unsigned char *lcd = hp48_get_lcd(&rows, &stride);
    printf("create ok; lcd=%p rows=%d stride=%d\n", (const void *)lcd, rows, stride);

    /* '1' key: matrix code 0x13 -> row 1, column bit (1 << 3) = 0x08.
     * (See docs/keymap.md.) */
    hp48_press_key(0x13);
    printf("press '1': keybuf row1 = 0x%x\n", saturn.keybuf.rows[1]);
    hp48_release_key(0x13);
    printf("release '1': keybuf row1 = 0x%x\n", saturn.keybuf.rows[1]);

    /* ON key (0x8000) sets bit 15 across all rows. */
    hp48_press_key(0x8000);
    printf("press ON: row0 bit15 = %d\n", (saturn.keybuf.rows[0] & 0x8000) ? 1 : 0);
    hp48_release_key(0x8000);

    hp48_destroy(h);
    printf("destroy ok\n");
    return 0;
}
