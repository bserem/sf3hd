# Release scope: experimental only

All work in this survey is retained for research and excluded from the official
first release. Release v1 packs stages and characters only. See README for the
current release workflow.

# Non-character, non-stage asset survey

Inventory of the remaining upscale targets: menus, HUD, the VS and loading
screens and the character select portraits. Entry indices are from the local
`SF33RD.AFS` and 3sx's own tables.

**Status: route A is decoding.** `tools/decode_screens.py` extracts 14 of the 16
standalone screens into `work/decoded/<stem>/chunk_NN.png`;
`tools/verify_screens.py` re-derives the source bytes independently and passes.
`SelectNew.ppg` and `Opening.ppg` remain undecoded because their palettes are
not in the file. Route B (the `efNN.bin` HUD groups) is not started.

There are two distinct containers, and they need different routes.

## Opening HD update

Opening now decodes all 90 chunks against `title.bin` and has a 4× indexed
replacement route. The historical unresolved status below is superseded for
Opening; SelectNew remains unresolved. See README's “Opening at 4×” commands.
The original transfer records remain byte-identical, preserving palette banks,
rectangle order and logical geometry. A patched renderer recognizes indexed
`formARGB = 0x480N` and uses a logical grid at 1/N the texture resolution.
The ordinary direct32 screen builder still rejects Opening intentionally.

Validation: 90 packed chunks passed format, palette-index and transfer-table
checks; the rebuilt archive passed all 1,535 entry checks (36 replacements).
Vulkan playback reached the 4× indexed drawing path. Full visual review remains
outstanding.

## Route A: standalone `.ppg` screens

These are ordinary PPG containers, the same format `decode_stages.py`,
`export_for_upscale.py`, `cut_pages.py` and `ppg_repack.py` already handle for
stages. They are loaded by **AFS entry index**, not by texture group, so an
in-place `afs_rebuild.py` replacement works exactly as it does for stages: for
example `demo00.c:282` loads entry 12 as `Warning.ppg` and `sc_sub.c:213` loads
entry 10 as `scrscrn.ppg`.

| Entry | Name | Chunks | Chunk sizes | Pixel format | What it is |
|---:|---|---:|---|---|---|
| 0 | `Rank1.ppg` | 8 | 128x128 | 16bpp direct | Ranking screen |
| 1 | `Rank2.ppg` | 8 | 128x128 | 16bpp direct | Ranking screen |
| 2 | `Select.ppg` | 16 | 128x128 | 16bpp direct | Character select |
| 3-6 | `Win.ppg`, `Win1-3.ppg` | 16 each | 128x128 | 16bpp direct | Win/results screens |
| 8 | `Ake3rd.ppg` | 3 | 128x256 | 8bpp indexed | Akane (stage07 tie-in) |
| 10 | `scrscrn.ppg` | 7 | 128x128, 256x128, 256x256 | 4bpp x5, 8bpp x2 | **The in-game HUD**: health bars, super gauge, stun meter, portraits, names, timer font |
| 11 | `SelectNew.ppg` | 24 | 128x128 | 8bpp indexed | Character select portraits |
| 12 | `Warning.ppg` | 2 | 512x512 | 8bpp indexed | Warning screen |
| 13 | `Warninge.ppg` | 4 | 512x256, 512x512 | 8bpp x2, 16bpp x2 | Warning screen (English) |
| 75 | `CapLogo.ppg` | 1 | 256x256 | 4bpp indexed | Capcom logo |
| 76 | `Opening.ppg` | 90 | 256x256 | 8bpp indexed | Opening/attract movie frames |
| 78 | `TitleTM.ppg` | 1 | 512x256 | 16bpp direct | Title screen |
| 79 | `Title_R.ppg` | 1 | 512x256 | 16bpp direct | Title screen |
| 1445, 1450 | `Bonus00/01.ppg`, `stageNN.ppg` | - | - | - | already repacked at 4x |

### What `decode_screens.py` does

It walks the chunk list, decodes each pTEX independently (there is no mask or
layer assembly, because each chunk is its own texture), and writes
`work/decoded/<stem>/chunk_NN.png` plus a manifest whose `chunks` array is
shaped like the stage manifests, so `check_upscaled.py` gates these folders
unchanged.

Formats handled, all transcribed from `ppgSetupContextFromPPG` and
`ppgSetupPalChunk`:

- **16bpp direct**, `formARGB` 0x1555 — red at shift 10, no R/B swap. This is
  the majority of the screens and needs no palette at all.
- **Endianness.** 16- and 32-bit data is stored big-endian and byte-swapped on
  load unless the "already little-endian" flag is set — `pixel & 4` for a pTEX,
  `c_mode & 4` for a pPAL (`ppgChangeDataEndian`). No stock screen sets that
  flag, so the swap always applies. 8bpp and 4bpp index planes return early and
  are never touched, but the **palettes** that go with them are 16-bit and are.
- **8bpp and 4bpp indexed** with an embedded `pPAL` chunk. For 4bpp the low
  nibble is the even pixel, matching `fetch4bppIndex` in
  `shaders/opengl/frag_palette_4.glsl`.
- **Trans tables**, where present, select a bank per sub-rect exactly as the
  stages do. Area no trans record covers is undrawn and stays transparent —
  `CapLogo.ppg` draws 59.4% of its chunk that way.

### Which palette goes with which texture

The file does not record this. `ppgSetupPalChunk` takes the palette ordinal
from the caller, and `sc_sub.c:218-221` binds `scrscrn.ppg`'s four palettes to
four different draw lists, so the pairing is caller state. `select_palette()`
recovers it from the file where the file is unambiguous and says so when it is
not, recording the rule it used in each chunk's manifest record:

| Rule | When | Example |
|---|---|---|
| `only-palette` | one palette wide enough for the indices used | `Warning`-style singles |
| `bank-per-chunk` | that palette holds exactly one bank per chunk | `Ake3rd.ppg`, 3 chunks / 3 banks |
| `palette-per-chunk` | as many candidate palettes as same-depth chunks | `Warninge.ppg` |
| `ambiguous` | more candidates than that; first is used and a note is printed | `scrscrn.ppg`'s 4bpp chunks |

### Previews

`work/screens-preview/<stem>.png` is a contact sheet per screen: every chunk in
order, transparency drawn as a checkerboard. A screen's chunks are separate
textures with no stored layout, so this identifies content; it is not a
reconstruction of the assembled screen. `--no-sheet` skips it. Sheets live
outside the chunk folders so `check_upscaled.py` still gates a folder holding
exactly the chunks.

### Verification

`tools/verify_screens.py` re-reads the archive rather than trusting the
decoder. Direct chunks are re-encoded to ARGB1555 and compared byte-for-byte
with the inflated payload, which proves pitch, size, channel shifts and the
5-bit expansion are exactly invertible. Indexed chunks are checked
structurally: every decoded colour must belong to the palette actually used,
and every index must fit its bank.

A round trip alone is not enough, and the first version of the decoder proved
it: skipping `ppgChangeDataEndian` scrambled the alpha bit and both colour
channels, yet every chunk still re-encoded byte-identically, because the
inverse of a wrong read is a matching wrong write. `endian_sanity()` closes
that gap without looking at the picture — a byte swap moves the alpha bit out
of bit 15, so a screen that should be opaque reads as nearly transparent.
`TitleTM.ppg` is 100% opaque decoded correctly and 9.1% opaque without the
swap. `tests/test_screens.py` locks in both the conversion and the check.

Current result: **100 direct chunks re-encode byte-identically, 15 indexed
chunks structurally consistent, 0 screens with problems.**

### Still blocked

- **`SelectNew.ppg` (24 chunks) and `Opening.ppg` (90 chunks)** carry trans
  tables but no `pPAL`. Their banks come from ColorRAM: `opening.c:200` sets
  `ppgOpnBgList.pal = palGetChunkGhostCP3()`, and that "ghost" palette is
  allocated zeroed in `color3rd.c:497-509` and filled at runtime. Finding the
  file that supplies those banks is the remaining work, and it is the same
  mechanism the stages resolve through their `bgNN0.bin` pairing. Until then
  the decoder reports them and writes nothing.
- **Upscaling still needs a page-assembly decision.** `export_for_upscale.py`
  assembles stage pages from stage slot layout. Screen chunks are independent
  textures drawn as their own quads, so per-chunk upscaling is the correct unit
  here — but adjacent chunks of one screen image will each be upscaled in
  isolation, so edge behaviour at the seams needs checking before repacking.

## Route B: `efNN.bin` texture groups

These use the same CG container as `plNN.bin`, so `cg_decode_all.py`,
`cg_hd_pack.py` and the `.hdcg` sidecar loader apply, but each needs its
`to_tex`/`to_chd`/palette/group metadata added to `TEXGROUP` in
`tools/cg_decode_tile.py`, taken from `texgrpdat[]` in the engine's
`texgroup_data.c`. The values there are compiled into the engine, so they must
match the archive being loaded.

| Group | AFS entry | Bytes | Mode | Notes |
|---:|---|---:|---|---|
| 21 | `ef00.bin` | 1,484 | NORMAL | |
| 98 / 23 | `ef02.bin` / `ef02_usa.bin` | 1,126,132 / 1,128,236 | NORMAL | Region-selected pair (`texgroup.c:261-263`); the localized text/UI group |
| 25 | `ef05.bin` | 176,652 | NORMAL | |
| 26 | `ef06.bin` | 595,800 | NORMAL | |
| 27, 89-97 | `ef07_A`-`ef07_J.bin` | 88,156 each | SHARED | Ten identical-size copies, one per loaded slot |
| 30 | `ef25.bin` | 35,820 | NORMAL | |
| 33 | `ef32.bin` | 2,148,440 | NORMAL | Largest effect group |
| 34 | `ef34_A.bin` (`_B` at entry 1449) | 36,244 | NORMAL | Bonus-stage pair |
| 35 | `ef36.bin` | 147,800 | SHARED | |
| 38 | `ef40.bin` | 170,688 | NORMAL | |
| 82 | `ef65.bin` | 621,536 | NORMAL | |
| 83 | `ef70.bin` | 35,828 | NORMAL | |
| 84 | `ef71.bin` | 135,264 | NORMAL | |

The HUD is drawn from these groups rather than from a `.ppg` screen. Which
group holds the vital gauge and round timer is **not yet confirmed** — the
survey above stops at the container level. `ef02`/`ef02_usa` is the strongest
candidate because it is the only region-selected pair, which is what localized
on-screen text implies. Confirm by decoding a group and looking at the frames
before adding it to the pipeline.

Cache assignment is worth noting for the HD path: `bbbscom2.c` sets the two
player caches (3 and 4), and those are the only caches `hdCgScaleForCache`
grows. Effect and UI objects use caches 5-17 (`eff*.c`, `aboutspr.c`), which
stay 256x256. Any HD sidecar for an `ef` group therefore needs its destination
cache added to `hdCgScaleForCache` and its memory budget checked first;
otherwise the tiles are correctly rejected and fall back to stock, as the
opening's cache 9 melt already does.

## The player select screen (`ef02.bin` / `ef02_usa.bin`)

The big character portrait, the round face icons and the "PLAYER SELECT"
lettering are **not** in any `.ppg`. They are the "SelObj" group:
`reservMemKeySelObj` (`texgroup.c:243`) reserves exactly 1,128,236 bytes -- the
size of `ef02_usa.bin` -- and `checkSelObjFileLoaded` picks `texgrpdat[98]`
(`ef02.bin`, Japanese) or `texgrpdat[23]` (`ef02_usa.bin`, everything else).
The objects draw into **texture cache 13** (`eff69.c:175`).

`Select.ppg`, which the pipeline already replaces, is the screen's background
art only. `SelectNew.ppg` is a separate 24-chunk set.

### Structure, verified against the local archive

Effect and object groups carry no `to_chd` (that field is character-only, for
fighter init data). Their sprite composition lives at **file offset 0**:
`texgroup.c:141-142` sets `trans_table = ldadr` and
`texture_table = ldadr + to_tex`. `mtrans.c:181-184` then resolves a pattern:

```
n       = pattern_number - texgrpdat[group].num_of_1st   # 27104 for SelObj
trsbas  = trans_table + ((u32*)trans_table)[n]
count   = *trsbas                                        # pieces in this pattern
trsptr  = (TileMapEntry*)(trsbas + 1)
texptr  = texture_table + ((u32*)texture_table)[trsptr->code]
```

Both tables are self-describing: the first u32 offset divided by four is the
entry count, the same trick `cg_decode_tile.py` already uses. Parsing the local
archive that way gives:

| | patterns | tiles | tile sides (32/16/8) |
|---|---:|---:|---|
| `ef02.bin` | 1,863 | 5,710 | 3,700 / 1,729 / 281 |
| `ef02_usa.bin` | 1,863 | 5,739 | 3,735 / 1,726 / 278 |

Piece counts of the first patterns are 4, 5, 3, 4, 5, 7 -- sane sprite
composition, so the reading is correct.

### The palette source, and what it unlocks

These groups have no `plNNpl.bin`; their banks come from ColorRAM, which is why
`SelectNew.ppg` and `Opening.ppg` also could not be decoded. `color_file[161]`
in `color3rd.c:566` is the map, read by `load_any_color` /
`q_ldreq_color_data`: each row is an AFS file number plus a `type` and a
destination. Type 2 copies palette banks into ColorRAM at bank `data`:

| Row | AFS entry | ColorRAM bank | Covers |
|---|---|---|---|
| 20 | `default.bin` (61,440 B = 480 banks of 64) | 0x20 | the system/object palettes |
| 21-40 | `bgNN0.bin`, `bns00/10.bin` | 0x12C | stages, already used by `decode_stages.py` |
| 84 | `title.bin` | 0x12C | title |
| 89-108 | `endNN0.bin` | 0x12C | endings |

`default.bin` at bank 0x20 supplies the selection palettes. Its file words
are **ARGB1555** (R at bit 10, B at bit 0), as defined by `palCreateGhost`.
`palConvSrcToRam` converts them to the engine's internal format; a decoder
reading the file directly must not swap red and blue.

### Decoding selection objects

```sh
python3 tools/decode_objects.py SF33RD.AFS --group ef02_usa.bin
```

Output is `work/decoded/ef02_usa/`, with a manifest and a contact sheet in
`work/screens-preview/`. For a small portrait sample, add
`--start 1431 --limit 2 --sheet-count 0`.

Cache 13's mode is 0x2012. `Mtrans_use_trans_mode` dispatches mode 18 to
`mlt_obj_trans_cp3`, whose ext branch calls **mlt_obj_trans_cp3_ext**.
The correct bank is `(base_bank + (piece.attr & 0x1FF)) & 0x1FF`.
The similarly named `mlt_obj_trans_ext` belongs to a different renderer;
its flat palette rule does not apply here. Nor is the piece offset limited
to a low nibble: the local archive contains offsets above 15.

Large portraits use base 144 (`eff38.c`, `my_col_code = 0x90`). This is the
default; `--bank` overrides it for objects with another base. `--piece-bank`
explicitly selects the default nine-bit rule; `--flat-bank` is a diagnostic
option. The manifest records the format, rule, and resolved bank per piece.
Other objects can require their runtime base, so this is not a guarantee
that every object in the group uses the portrait base.

### Remaining for a select-screen HD pack

1. Stage the frames through `export_for_upscale.py` / `upscale.py` /
   `cut_pages.py`. The manifest is already frame-shaped, so the character path
   applies -- and because whole frames are composited before upscaling, this
   avoids the per-tile seams that limit the UI screen route.
2. Pack with `cg_hd_pack.py`, which is keyed by tile code and needs the palette
   banks passed in from ColorRAM rather than a plNNpl.bin.
3. **Engine change**: `hdCgScaleForCache` grows caches 3 and 4 only. The select
   objects draw into cache 13 (`eff69.c:175`), so it needs to grow too, with a
   memory budget check -- ~5,700 tiles is comparable to one fighter.
4. Both region variants (`ef02.bin`, `ef02_usa.bin`) need packing, since
   `checkSelObjFileLoaded` picks by language at runtime.

## Fonts

`dynafont.bin` (780,800 B), `dynafnte.bin` (23,856 B) and `dynafntk.bin`
(185,188 B) at entries 80-82 are the menu fonts, drawn through
`ui/glyph_renderer.c`. These are glyph data rather than sprite sheets, so they
are a separate problem from the texture routes above and are likely better
served by rendering at a higher resolution than by upscaling bitmaps.

## Upscaling and repacking

`tools/build_screens.py` drives the whole screen route and is the script to run:

```sh
python3 tools/build_screens.py SF33RD.AFS --dry-run   # plan and projected sizes
python3 tools/build_screens.py SF33RD.AFS             # decode, upscale 4x, repack
python3 tools/build_hd_test.py                        # bundle
```

It runs decode -> verify -> `export_for_upscale` -> `upscale` -> `cut_pages` ->
`check_upscaled` -> `ppg_repack --mode direct32`, leaving one `.ppg` per screen
in `work/repacked/` next to the stage repacks. `build_hd_test.py` rebuilds the
archive from that whole folder, so the screens need no separate bundling step
and no engine change: these entries are loaded by AFS index, so replacing them
in the archive is the entire job. `--only`, `--skip-upscale`, `--skip-decode`,
`--scale` and `--dry-run` are available.

`export_for_upscale.py` and `cut_pages.py` copy screen chunks straight through
instead of assembling pages, keyed off `"kind": "screen"` in the manifest. Each
pTEX is an independent texture, so per-chunk is the correct upscale unit — but
adjacent chunks of one screen image are upscaled in isolation, so **check the
seams** on the visibly tiled screens (`Select`, `Win*`, `Rank*`) before
trusting them. `cut_pages.py` enforces a single integer scale per screen.

### Only palette-free screens can be replaced

`direct32` bakes one palette into the pixels. The engine does not work that
way: `sc_sub.c` has 18 `njSetPaletteBankNumG(page, atr & 0x3F)` sites, so the
palette bank is chosen **per draw call**, and `scrscrn.ppg`'s palettes declare
32 and 260 banks. Replacing it froze every bank-selected HUD element to one
palette and turned the health bars, timer, super gauge and portraits into
rainbow garbage in-game.

`direct32_safe()` in `build_screens.py` therefore refuses any screen that
carries an embedded `pPAL`, a trans table, or indexed pixels. What remains is
the nine screens that are already 16bpp direct colour and reference no palette
at all:

| Replaced | Not replaced (palette-driven) |
|---|---|
| `Rank1`, `Rank2`, `Select`, `Win`, `Win1`, `Win2`, `Win3`, `TitleTM`, `Title_R` | `scrscrn` (HUD), `Warning`, `Warninge`, `CapLogo`, `Ake3rd` |

Upscaling the palette-driven five needs a route that keeps them indexed and
preserves every bank, which `--mode indexed` does not do: it re-quantises
against a `bgNN0.bin` palette these screens do not have, and caps at 2x.

**Engine memory.** A 512x512 screen chunk at 4x decompresses to 16 MiB in one
piece, four times the largest stage chunk, so `HD_PPG_DECBUF` went from 8 MiB
to 32 MiB and `HD_TEXTURE_POOL` from 256 MiB to 384 MiB. Without that the
engine aborts at load in `ppgSetupTexChunk_3rd`. `build_screens.py` reads the
define out of `3sx/src/main.c` and refuses to repack screens the engine cannot
load, naming the chunk that needs the most. `--scale 2` quarters the
requirement if you would rather not grow the pools.

`--mode direct32` is required. `indexed` mode needs a `bgNN0.bin` palette these
screens do not have and caps at 2x. direct32 depends on the engine's GS alpha
fix, which is already in the local `3sx/` checkout.

Across the nine replaceable screens the dry run projects ~112 MiB of 32-bit
texels at 4x before zlib; the packed total is much smaller, since `build_tex`
compresses and UI art is mostly flat colour.

## Remaining order

1. Resolve the ColorRAM palette source for `SelectNew.ppg` and `Opening.ppg`,
   the last two route A screens.
2. Run `build_screens.py` over all 14, then check the tiled screens for seams.
3. `ef02`/`ef02_usa` for the HUD, after confirming its contents and after
   deciding whether to grow a UI cache to HD.
