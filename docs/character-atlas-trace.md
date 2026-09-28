# Character atlas allocation and upload trace

Current local implementation, 2026-09-09. The indexed HD sidecar is implemented
for all 20 character groups at 4x and smoke-tested on Vulkan and OpenGL. See
[hd-test-build.md](hd-test-build.md) for launch/rebuild commands and validation limits.
Function names below are the lookup references; line numbers shift with edits.
Paths are relative to `3sx/src/`.

## Finding and implemented fix

The original path coupled packed logical dimensions to texel dimensions and a
256x256 atlas. Increasing the original CG `wh` enlarged the fighter. The HD path
keeps `wh`, piece geometry, anchors and cache indices unchanged and supplies a
separate physical texture density. At 4x, page sides are 1024 and small/large
slot sides are 64/128. There is no 2x ceiling in this PC atlas layout.

## Allocation to draw

1. `sf33rd/Source/Game/rendering/hd_cg.c`, `hdCgScaleForCache`: loads the optional
   `THREESX_HD_CG` sidecar once. Header, directory, bounds and palette indices
   are validated before enabling density 2 or 4. Missing/invalid packs give
   density 1. Only player cache IDs 3 and 4 grow.
2. `sf33rd/Source/Game/rendering/texcash.c`, `make_texcash_work`: retains
   `page16 * 256` small cache entries and `page32 * 64` large entries. CPU
   staging allocation is `mltnum * 65536 * bytes_per_pixel * density^2`.
   The cache ID is set before initializing its textures.
3. `sf33rd/Source/Game/rendering/mtrans.c`, `mlt_obj_trans_init`: synthesizes
   a PPG header with width/height `16 * density`. Header dimensions are in
   16-pixel units, so density 4 produces 1024x1024 contexts. Indexed modes
   use pixel `0x81`; RGB uses `0x82`.
4. `sf33rd/Source/Common/PPGFile.c`, `ppgSetupTexChunkSeqs`: derives context
   pitch and height, sets `srcSize = pitch * height`, clears each staging page,
   creates its handle, and sets `tch->be = 1` after successful setup. The old
   notes' suspected missing initialization was resolved here.
5. `sf33rd/AcrSDK/ps2/flps2vram.c`, `flCreateTextureHandle`: context dimensions
   determine FLTexture width/height, backing size and allocation. GPU creation
   receives that metadata.
6. `mtrans.c`, the character transfer variants: resolve the original group/tile
   key to a cache slot. The original `wh` still supplies logical dw/dh and stock
   decode length. Small slots use page `code >> 8`, index `code & 255`; large
   slots use page `code >> 6`, index `code & 63`. All 14 former upload calls,
   including RGB, extended caches and `mlt_obj_melt2`, call `hdCgUpload` with
   original group/tile identity before any tile-code rewrite.
7. `hd_cg.c`, `hdCgUpload`: for a matching group, code, wh and destination cache
   density, supplies
   linear HD indices to `ppgRenewLinearDotDataSeqs`. RGB paths convert indices
   through the current ColorRAM palette into separate scratch storage. Other
   cases use the original decoded pixels through `ppgRenewDotDataSeqs`.
8. `PPGFile.c`, `ppgRenewDotDataSeqs`: decodes the original byte-count convention
   into logical side and bytes per pixel, unswizzles within the stock 32x32
   Morton domain, and calls the linear-copy path at source density 1. HD byte
   counts never enter this legacy dispatch: a 2x small tile would otherwise
   be mistaken for a stock large tile.
9. `PPGFile.c`, `ppgRenewLinearDotDataSeqs`: gets destination density from the
   actual FLTexture width, validates page/copy dimensions, and calls
   `hd_atlas.h`'s `hdAtlasCopy`. Slot class follows logical side, row stride
   follows physical page width, and stock pixels expand by nearest-neighbor
   when copied into an HD page. The page is then marked dirty (`0x2000`).
10. `mtrans.c` calls `ppgRenewTexChunkSeqs` before drawing. It locks each dirty
    texture, copies the whole staging page using `srcSize`, and unlocks it.
    `flUnlockTexture` reaches renderer unlock. OpenGL recreates the texture
    with `glTexImage2D`; SDL GPU queues recreation. Both use FLTexture dimensions
    and matching byte counts, with no hardcoded 256x256 backend atlas size.
11. `mtrans.c`, `seqsStoreChip`: geometry remains `x..x+w`, `y..y-h` followed by
    the object matrix. UV origins/spans multiply by density and normalize by
    `256 * density`. Flip swaps, palette, brightness and alpha retain their
    original roles. A scaled-object inset is half a physical texel. Canvas
    scale is distinct from object scale and does not itself trigger the inset.

## Coordinated dimension contract

Keep logical piece width/height, deltas, anchors, object transforms, collision
data, cache slot counts and page selection unchanged. Texture density S is separate from those values and is known before allocation:
1 with no valid pack, or 2/4 from the sidecar header.

| Quantity | 1x | 2x | 4x |
|---|---:|---:|---:|
| Physical page side | 256 | 512 | 1024 |
| Physical small slot side | 16 | 32 | 64 |
| Physical large slot side | 32 | 64 | 128 |
| Small / large slots per page | 256 / 64 | 256 / 64 | 256 / 64 |
| Indexed bytes per page | 65,536 | 262,144 | 1,048,576 |
| RGB16 bytes per page | 131,072 | 524,288 | 2,097,152 |

For small slots, column = `slot & 15`, row = `slot >> 4`; for large slots,
column = `slot & 7`, row = `slot >> 3`. Physical origin is column/row times
the physical slot side. CPU destination byte offset is
`page * page_bytes + (physical_y * page_width + physical_x) * bytes_per_pixel`.
Row stride is `page_width * bytes_per_pixel`.

Geometry stays in logical units. UV bounds become:

```
u0 = (logical_slot_x * S + inset_texels) / (256 * S)
u1 = ((logical_slot_x + w) * S - inset_texels) / (256 * S)
v0 = (logical_slot_y * S + inset_texels) / (256 * S)
v1 = ((logical_slot_y + h) * S - inset_texels) / (256 * S)
```

Apply existing flip swaps after calculating the endpoints. With zero inset,
normalized UVs are mathematically unchanged: the larger atlas supplies more
texels to the same quad. A half-physical-texel inset is `0.5 / (256*S)`,
not `0.5 / 256`. Preserve the stock 1x behavior and validate HD seams under
object scaling separately; canvas scale is distinct from the object matrix.

## Payload and cache behavior

`cg_hd_pack.py` writes a versioned linear indexed sidecar keyed by original tile
code within one group, one file per character. `TEXGROUP` in
`tools/cg_decode_tile.py` maps each archive entry to its texgroup id.
Density and payload lengths are separate from the original logical wh byte;
no AFS character entry, expanded Morton table or larger shared stock decode
buffer is required. The binary layout is in [hd-test-build.md](hd-test-build.md).

Each of the two player caches has three small-slot pages and six large-slot
pages. At 4x this is 9 MiB of indexed CPU staging per player, plus FLTexture
backing and GPU storage. Other characters still use stock source pixels, scaled
into those pages as needed; UI, stage and effect caches do not grow with this
sidecar. Character groups uploaded into a 1x cache -- the opening's
`mlt_obj_melt2` targets cache 9 -- keep their stock pixels, because HD tiles do
not fit a 1x page. All 20 packs stay loaded for the process lifetime, about
2.4 GiB; restart to change them. RGBA replacement loading is not implemented.

## Validation and remaining work

Completed checks include 5,760 synthetic slot copies under ASan/UBSan across
1x/2x/4x, 8/16/32 logical sides, indexed/RGB and stock/HD sources; malformed
loader inputs and fallback; stale-scale regeneration; six reconstructed sample
frames; the 1x-cache fallback; and Vulkan/OpenGL gameplay smoke tests
confirming 1024x1024 player atlases. See the test guide for exact scope and commands.

Full animation/costume review, both-player match coverage and performance
profiling remain. Frame-to-tile export is not assumed lossless: shared tiles
select their most-visible occurrence, hidden regions retain stock pixels, and
RGB is quantized into original palette banks. Dirty-page CPU copies and GPU
recreation transfer density squared as many bytes; subregion uploads remain
an optimization to evaluate after profiling.
