# tools

Helper scripts for libcalc48 (not built into the library).

## make_blank_state.py — build the embedded blank state for rpl_eval

`examples/rpl_eval.py` boots a *pristine* calculator from a user-supplied ROM
plus an **embedded blank RAM/CPU image** (a cold boot from zeroed RAM doesn't
reach a usable state headlessly).  That image is ROM-revision specific and
**not committed** (it's ROM-derived — `examples/_blank_state.py` is gitignored),
so generate it locally:

```sh
HP48_LIB=build/libcalc48.so python3 tools/make_blank_state.py ~/.hp48
```

It loads a *seed* saved-state dir (a working calc of the same ROM revision),
clears the stack + purges all HOME variables, and writes the `hp48`+`ram` blobs
(gzip+base64) into `examples/_blank_state.py`.  The ROM is never written.
Re-run it whenever your ROM revision changes.

## extract_fonts.py — dump the HP 48 GX system fonts

Drives the emulator to render every character through the calculator's own
`→GROB` command and captures the exact glyph bitmaps for the loaded ROM — no ROM
disassembly, no screen-scraping. Useful for building a glyph-recognition /
high-res-substitution renderer for the LCD (replace pixelated text with crisp
characters when scaling the display up).

Captures three font sizes (`→GROB`'s size argument):

| size | name       | notes                                            |
|------|------------|--------------------------------------------------|
| 1    | `tiny`     | the minifont (proportional, ~6 px tall)          |
| 2    | `standard` | the 6×8 stack font                               |
| 3    | `large`    | the large font (covers only codes `0x20`–`0x81`) |

### Why it needs a one-time setup

`→GROB` can't be typed headlessly (it needs the `→` key) and a *compiled* call
to it is ROM-version specific. So the tool is **self-calibrating**: you put the
program `« →GROB »` on stack level 1, and it reads that back to recover the exact
compiled word-sequence for `→GROB` on your ROM. It then builds, in memory,
`« "<char>" <size> →GROB »` per character, pushes it with the stack API
(`hp48_stack_push_object`), presses EVAL, and reads the resulting grob back
(`hp48_read_object`). Non-destructive — it loads the state but never saves it.

### Usage

1. On the calc (or in x48), put **`« →GROB »`** on level 1 and save the state.
2. Build libcalc48 with the stack API (`HP48_WITH_STACK_IO=ON`, the default).
3. Run:

   ```sh
   HP48_LIB=./build/libcalc48.so \
     python3 tools/extract_fonts.py ~/.hp48 tools/hp48_fonts_gx.json
   ```

   Both args are optional (`STATE_DIR` defaults to `~/.hp48`, output to
   `tools/hp48_fonts_gx.json`). GX only.

### Output — `hp48_fonts_gx.json`

```json
{
  "model": "GX",
  "grob_call_words": ["2361e", "1e5ad", "23639"],
  "fonts": {
    "tiny":     { "65": { "w": 4, "h": 6, "data": "…" }, … },
    "standard": { … },
    "large":    { … }
  }
}
```

Each glyph: pixel `width`/`height` and `data` = the grob's pixel nibbles (one
nibble per hex char, **LSB = leftmost pixel**, row-major). Reconstruct a row at
`stride = 2*ceil(w/8)` nibbles (rows are padded to a byte). Keys are decimal HP
character codes; map them to Unicode via the
[RPL character set](https://en.wikipedia.org/wiki/RPL_character_set).

`hp48_fonts_gx.json` is the captured result for one GX ROM revision, checked in
for convenience; re-run the tool for a different ROM.
