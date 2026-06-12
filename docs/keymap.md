# HP 48 key matrix codes

This maps the HP 48 keys to the **matrix codes** understood by the emulator's
input API — `hp48_press_key(code)` / `hp48_release_key(code)` — and shows the
PC-keyboard bindings the SDL front end uses.

Derived from `sdl/src/x48_sdl.c` (`buttons_gx[]` and `sdltohpkeymap[]`).

## Code format

A key is a position in the calculator's keyboard matrix:

```
row     = code >> 4          // 0..8
column  = code & 0x0f        // bit index, 0..5
```

`hp48_press_key()` sets bit `(1 << column)` in `keybuf.rows[row]`;
`hp48_release_key()` clears it.

The **ON** key is special: code **`0x8000`**, which sets bit 15 in *every* row.

```c
hp48_press_key(0x14);    /* A  */     hp48_press_key(0x8000);  /* ON */
hp48_release_key(0x14);               hp48_release_key(0x8000);
```

## Keys by physical layout

| Key | Code | Row | Col | Notes |
|-----|------|----:|----:|-------|
| A      | `0x14` | 1 | 4 | menu key |
| B      | `0x84` | 8 | 4 | menu key |
| C      | `0x83` | 8 | 3 | menu key |
| D      | `0x82` | 8 | 2 | menu key |
| E      | `0x81` | 8 | 1 | menu key |
| F      | `0x80` | 8 | 0 | menu key |
| MTH    | `0x24` | 2 | 4 | |
| PRG    | `0x74` | 7 | 4 | |
| CST    | `0x73` | 7 | 3 | |
| VAR    | `0x72` | 7 | 2 | |
| UP (▲) | `0x71` | 7 | 1 | |
| NXT    | `0x70` | 7 | 0 | |
| ' (COLON) | `0x04` | 0 | 4 | |
| STO    | `0x64` | 6 | 4 | |
| EVAL   | `0x63` | 6 | 3 | |
| LEFT (◀) | `0x62` | 6 | 2 | |
| DOWN (▼) | `0x61` | 6 | 1 | |
| RIGHT (▶) | `0x60` | 6 | 0 | |
| SIN    | `0x34` | 3 | 4 | |
| COS    | `0x54` | 5 | 4 | |
| TAN    | `0x53` | 5 | 3 | |
| √ (SQRT) | `0x52` | 5 | 2 | |
| yˣ (POWER) | `0x51` | 5 | 1 | |
| 1/x (INV) | `0x50` | 5 | 0 | |
| ENTER  | `0x44` | 4 | 4 | double-width |
| +/− (NEG) | `0x43` | 4 | 3 | |
| EEX    | `0x42` | 4 | 2 | |
| DEL    | `0x41` | 4 | 1 | |
| ⌫ (BS) | `0x40` | 4 | 0 | backspace |
| α (ALPHA) | `0x35` | 3 | 5 | |
| 7      | `0x33` | 3 | 3 | |
| 8      | `0x32` | 3 | 2 | |
| 9      | `0x31` | 3 | 1 | |
| ÷ (DIV) | `0x30` | 3 | 0 | |
| ◤ (SHL, left shift) | `0x25` | 2 | 5 | |
| 4      | `0x23` | 2 | 3 | |
| 5      | `0x22` | 2 | 2 | |
| 6      | `0x21` | 2 | 1 | |
| × (MUL) | `0x20` | 2 | 0 | |
| ◥ (SHR, right shift) | `0x15` | 1 | 5 | |
| 1      | `0x13` | 1 | 3 | |
| 2      | `0x12` | 1 | 2 | |
| 3      | `0x11` | 1 | 1 | |
| − (MINUS) | `0x10` | 1 | 0 | |
| ON     | `0x8000` | all | 15 | special |
| 0      | `0x03` | 0 | 3 | |
| . (PERIOD) | `0x02` | 0 | 2 | |
| SPC    | `0x01` | 0 | 1 | |
| + (PLUS) | `0x00` | 0 | 0 | |

## The matrix grid

Each cell is the key at that `(row, column)`; the hex is the full code.

| row \ col | 0 | 1 | 2 | 3 | 4 | 5 |
|----:|----|----|----|----|----|----|
| **0** | + `0x00` | SPC `0x01` | . `0x02` | 0 `0x03` | ' `0x04` | — |
| **1** | − `0x10` | 3 `0x11` | 2 `0x12` | 1 `0x13` | A `0x14` | SHR `0x15` |
| **2** | × `0x20` | 6 `0x21` | 5 `0x22` | 4 `0x23` | MTH `0x24` | SHL `0x25` |
| **3** | ÷ `0x30` | 9 `0x31` | 8 `0x32` | 7 `0x33` | SIN `0x34` | ALPHA `0x35` |
| **4** | BS `0x40` | DEL `0x41` | EEX `0x42` | NEG `0x43` | ENTER `0x44` | — |
| **5** | INV `0x50` | POWER `0x51` | SQRT `0x52` | TAN `0x53` | COS `0x54` | — |
| **6** | RIGHT `0x60` | DOWN `0x61` | LEFT `0x62` | EVAL `0x63` | STO `0x64` | — |
| **7** | NXT `0x70` | UP `0x71` | VAR `0x72` | CST `0x73` | PRG `0x74` | — |
| **8** | F `0x80` | E `0x81` | D `0x82` | C `0x83` | B `0x84` | — |

ON (`0x8000`) is not part of the grid — it asserts bit 15 across all rows.

## PC keyboard bindings (SDL front end)

How `sdl/src/x48_sdl.c` maps host keys to HP 48 keys.

| PC key | HP 48 key | Code |
|--------|-----------|------|
| `0`–`9`, keypad `0`–`9` | 0–9 | `0x03,0x13,0x12,0x11,0x23,0x22,0x21,0x33,0x32,0x31` |
| `A`–`F` | A–F | `0x14,0x84,0x83,0x82,0x81,0x80` |
| `G` | MTH | `0x24` |
| `H` | PRG | `0x74` |
| `I` | CST | `0x73` |
| `J` | VAR | `0x72` |
| `K`, `↑` | UP | `0x71` |
| `L` | NXT | `0x70` |
| `M` | ' (COLON) | `0x04` |
| `N` | STO | `0x64` |
| `O` | EVAL | `0x63` |
| `P`, `←` | LEFT | `0x62` |
| `Q`, `↓` | DOWN | `0x61` |
| `R`, `→` | RIGHT | `0x60` |
| `S` | SIN | `0x34` |
| `T` | COS | `0x54` |
| `U` | TAN | `0x53` |
| `V` | SQRT | `0x52` |
| `W` | POWER | `0x51` |
| `X` | INV | `0x50` |
| `Y` | NEG | `0x43` |
| `Z` | EEX | `0x42` |
| `Space` | SPC | `0x01` |
| `Enter`, keypad `Enter` | ENTER | `0x44` |
| `Backspace` | BS | `0x40` |
| `Delete` | DEL | `0x41` |
| `.`, keypad `.` | PERIOD | `0x02` |
| `+`, keypad `+` | PLUS | `0x00` |
| `-`, keypad `-` | MINUS | `0x10` |
| `*`, keypad `*` | MUL | `0x20` |
| `/`, keypad `/` | DIV | `0x30` |
| `Esc` | ON | `0x8000` |
| `Left/Right Shift` | SHL (left shift) | `0x25` |
| `Left/Right Ctrl` | SHR (right shift) | `0x15` |
| `Left/Right Alt` | ALPHA | `0x35` |
