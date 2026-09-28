# Archived experimental workflows — excluded from release v1

This preserves the pre-v1 mixed-asset workflow for reference. Commands below
are historical: `build_hd_test.py` now builds only stages and characters into
`work/release-v1`, and cannot bundle the experimental repacks. Existing
`work/hd-test` data is retained. UI, HUD, portraits, menus, logos and Opening
remain experimental and are excluded from v1 because of unresolved issues.

# sf3-hd — an HD texture pipeline for Street Fighter III: 3rd Strike on 3SX

Tooling that reads the textures out of **your own** copy of `SF33RD.AFS`,
upscales them with a local AI model, and packs the results so that
[3SX](https://github.com/crowded-street/3sx) — the native PC port built on the
PS2 decompilation — renders the game at 4x texture density.

Currently replaced: **all 20 fighters**, **all 21 stages and bonus rooms**, and
**9 UI screens** (title, character select, win, rank), and the **player select
objects** (big portraits, face icons, lettering) as a sidecar.

---

## ⚠️ This repository contains no game assets

**It ships nothing from Street Fighter III, and it never will.**

- There is **no** `SF33RD.AFS` here, and no textures, sprites, palettes, audio
  or fonts extracted from one — not raw, not re-encoded, not "lightly edited",
  not upscaled.
- You must supply **your own legally obtained** `SF33RD.AFS`, from a PS2 disc
  or ISO you own, or from your copy of *Street Fighter III: 3rd Strike Online
  Edition* / the *Capcom Fighting Collection*. 3SX itself requires this for the
  same reason and ships no Capcom assets either.
- Everything this pipeline produces is derived from **your** file and stays on
  **your** machine, under the gitignored `work/` directory. `.gitignore`
  excludes `*.AFS`, `work/`, `output/` and `texture-pack/` so derived art
  cannot be committed by accident.
- **Do not redistribute the output.** A packed archive or sidecar contains
  Capcom's artwork, upscaled. Sharing it is distributing their assets. Share
  this repository and let people run it against their own copy.
- The AI model and the upscaler binary are third-party projects with their own
  licences (see [Dependencies](#dependencies)). They are **not** bundled here
  either; `setup.sh` checks for them and tells you where to get them.

If you are unsure whether something you want to do crosses into redistribution,
assume it does.

---

## What you need

| | |
|---|---|
| **Your own `SF33RD.AFS`** | 642,492,416 bytes for the common release. Other regions may work; the tools read the archive's own index rather than assuming offsets. |
| **A 3SX checkout** | `git clone https://github.com/crowded-street/3sx.git 3sx` |
| **A Vulkan-capable GPU** | Both for the upscaler and for 3SX. An integrated GPU is fine — the full character run took about 11 minutes on an Intel 890M. |
| **Disk space** | About **18 GB** under `work/`: 5.2 GB of upscaled PNGs, 4.9 GB of cut tiles, 2.4 GB of character sidecars, and a 3.5 GB bundle holding a second copy of the sidecars plus the rebuilt 1.2 GB archive. You can delete `work/upscaled` and `work/for_upscale` once `work/tiles` is built. |
| **RAM** | 8 GB to run the pipeline. **Roughly 3 GB while playing** — see [Memory](#memory-and-performance). |

Times below are from a Ryzen laptop with an Intel 890M iGPU. Yours will differ.

---

## Dependencies

Install these yourself; none are vendored:

| Dependency | Why | Where |
|---|---|---|
| Python 3.8+ | all the tooling | your distro |
| **Pillow** | every image operation | `pip install -r requirements.txt` |
| **upscayl-bin** | the ncnn/Vulkan upscaler CLI | [upscayl/upscayl](https://github.com/upscayl/upscayl) |
| **realesr-animevideov3-x4** | the upscale model | [upscayl/custom-models](https://github.com/upscayl/custom-models) |
| CMake + a C compiler | building 3SX | your distro |
| Git | cloning 3SX, applying the patch | your distro |

Check everything at once:

```sh
./setup.sh          # report what is present and what is missing
./setup.sh --venv   # also create .venv and install Pillow
```

If `upscayl-bin` or the models are not in a standard location, point at them:

```sh
export UPSCAYL_BIN=/path/to/upscayl-bin
export UPSCAYL_MODELS=/path/to/custom-models/models
```

`setup.sh` never downloads game data. It only checks for tools and tells you
whether your `SF33RD.AFS` is in place.

---

## Build it

Everything runs from the repository root. Put your archive there first:

```sh
cp /path/to/your/SF33RD.AFS .
```

### 1. Get the sources and dependencies

```sh
git clone https://github.com/crowded-street/3sx.git 3sx
./setup.sh --venv
. .venv/bin/activate
```

### 2. Patch 3SX

The engine needs changes to load HD textures: bigger memory pools, a larger PPG
decode buffer, a GS alpha fix, a 4x canvas, and the HD character atlas and
sidecar loader. They are in [`patches/3sx-hd-textures.patch`](patches/), against
upstream commit `513380f9`:

```sh
cd 3sx && git apply --3way ../patches/3sx-hd-textures.patch && cd ..
```

If upstream has moved on and the patch conflicts, `--3way` will leave conflict
markers to resolve. The same changes are also offered upstream as a pull
request. `patches/3sx-hd-textures.patch` touches 12 files and adds three new
ones (`hd_cg.c`, `hd_cg.h`, `hd_atlas.h`); CMake globs `src/*.c`, so no build
file changes are needed.

Configure a build directory. **Use RelWithDebInfo or Debug** — Release enables a
SHA-256 gate on the archive, and any modified archive necessarily fails it,
after which 3SX offers to overwrite your file from an ISO:

```sh
cmake -S 3sx -B 3sx/build-test -DCMAKE_BUILD_TYPE=RelWithDebInfo
```

### 3. Extract everything from your archive

```sh
# Stages and bonus rooms (~5 min)
python3 tools/decode_stages.py SF33RD.AFS

# All 20 fighters (~10 min). pl15 does not exist in the archive.
for cg in pl00 pl01 pl02 pl03 pl04 pl05 pl06 pl07 pl08 pl09 \
          pl10 pl11 pl12 pl13 pl14 pl16 pl17 pl18 pl19 pl20; do
  python3 tools/cg_decode_all.py SF33RD.AFS --cg "$cg.bin"
done

# UI screens (~1 min)
python3 tools/decode_screens.py SF33RD.AFS
python3 tools/verify_screens.py SF33RD.AFS

# Player select objects: portraits, face icons, "PLAYER SELECT" lettering
python3 tools/decode_objects.py SF33RD.AFS --group ef02_usa.bin
```

Output lands in `work/decoded/`. Contact sheets for identifying each UI screen
are written to `work/screens-preview/`.

### 4. Upscale (the long step)

```sh
python3 tools/export_for_upscale.py    # assemble stage pages, stage frames
python3 tools/upscale.py               # ~15 min total; skips finished folders
python3 tools/cut_pages.py             # cut pages back into per-chunk PNGs
python3 tools/check_upscaled.py        # gate: dimensions, format, binary alpha
```

`check_upscaled.py` must report **0 problems** before you go on. Re-run
`tools/upscale.py --force` to redo a folder, or `--scale N` to override.

### 5. Pack

```sh
# Stages -> work/repacked/*.ppg
python3 tools/ppg_repack.py SF33RD.AFS --mode direct32

# UI screens -> work/repacked/*.ppg (steps 3 and 4 already decoded and upscaled)
python3 tools/build_screens.py SF33RD.AFS --skip-decode --skip-upscale

# Player select objects -> a .hdcg sidecar, like a character
python3 tools/cg_hd_pack.py SF33RD.AFS --cg ef02_usa.bin --out work/hd-cg/ef02_usa.hdcg

# Characters, the archive rebuild, the engine build and the launcher
python3 tools/build_hd_test.py
```

Run `tools/build_screens.py SF33RD.AFS` with no flags to do the whole screen
route on its own — it will decode, verify, upscale and repack. Add `--dry-run`
first to see the plan and the projected sizes.

`build_hd_test.py` packs all 20 character sidecars, rebuilds the archive from
everything in `work/repacked/`, verifies **every** entry against the original,
builds and installs 3SX, and writes a launcher plus SHA-256 manifest. Allow
about 30 minutes; the character packing dominates.

### 6. Play

```sh
./work/hd-test/run.sh            # HD characters, stages and screens
./work/hd-test/run.sh --stock    # original character art, same HD stages
```

`--stock` only affects characters. Stage and UI replacements live inside the
rebuilt archive and are always active.

**Your installed game is untouched.** The launcher points `XDG_DATA_HOME` at
`work/hd-test/user-data/`, so it uses its own copy of the archive and its own
settings, and never writes to `~/.local/share/CrowdedStreet/3SX/`.

---

## How it works

Three different asset types need three different routes.

**Stages and UI screens** live in the archive as PPG containers and are
replaced in place. They are rewritten as 32-bit direct textures
(`--mode direct32`) and the archive is rebuilt with corrected entry offsets, so
the engine loads them with no special support. This is why upscaled stages need
no runtime feature at all.

**Characters** cannot work that way. Their tile format encodes the logical size
that also drives the fighter's on-screen geometry, so a bigger tile makes a
bigger fighter. Instead the pipeline writes a **sidecar** file per character
(`work/hd-test/hd-cg/plNN.hdcg`) holding 4x indexed pixels keyed by the original
tile id. The patched engine reads `THREESX_HD_CG`, grows the two player texture
caches from 256x256 to 1024x1024 pages, and keeps every logical dimension,
anchor and cache slot exactly as it was. The original archive's character data
is untouched.

**Alpha stays binary** everywhere. 3SX's fragment shader discards alpha-0
texels, so the upscaler handles colour and alpha separately: colour is bled
outward under the mask before upscaling to stop halos, and alpha is scaled and
re-thresholded.

Colour is quantised back into each frame's original 64-colour palette bank, so
alternate costumes still recolour correctly.

More detail: [`docs/hd-test-build.md`](docs/hd-test-build.md) (workflow and
validation), [`docs/character-atlas-trace.md`](docs/character-atlas-trace.md)
(allocation and upload), [`docs/ui-asset-survey.md`](docs/ui-asset-survey.md)
(screens and what is still unresolved), [`docs/format-notes.md`](docs/format-notes.md)
(container formats), [`docs/upscaling-notes.md`](docs/upscaling-notes.md)
(model choice and alpha handling).

---

## Memory and performance

The HD run needs roughly **3 GB of RAM**: measured peak RSS is 2.63 GB against
140 MB for a stock run. All 20 character sidecars (2.4 GB) are loaded at startup
and stay resident for the process lifetime. Loading each character's pack on
first use would cut this to the two fighters on screen; it is not implemented.

The engine patch also raises the pools: 512 MB master, 384 MB texture, and a
32 MB PPG decode buffer. That last one matters — `ppgSetupTexChunk_3rd`
decompresses one whole chunk at a time, and a 512x512 UI screen chunk at 4x is
16 MB. Too small a buffer aborts at load with *"Failed to allocate texture data
buffer"*; `build_screens.py` checks for this before it packs anything.

If you would rather not carry that, `tools/build_screens.py --scale 2` quarters
the screen requirement, and `tools/upscale.py --scale 2` does the same for the
rest.

---

## Verifying your build

```sh
python3 -m unittest discover -s tests -p 'test_*.py'
```

The archive rebuild is checked automatically: `verify_rebuild.py` re-parses both
archives independently and SHA-256s every entry, confirming that every
untouched entry is byte-identical and every replacement matches its source. A
full run replaces 35 of the archive's 1,535 entries (21 stages and bonus rooms,
14 UI screens) and leaves the other 1,500 alone.

`work/hd-test/manifest.json` records SHA-256 hashes for the executable, the
archive and each sidecar.

Successful character replacement logs one line per fighter:

```
HD CG: drawing group 3 in cache 3, 1024x1024 atlas
```

`HD CG: loaded ...` only means a pack was read. `HD CG: no valid pack for
group N` means that fighter stayed at 1x. `HD CG: rejected atlas copy: ...`
means a tile did not fit its destination page and fell back to stock.

---

## Known limitations

- **`SelectNew.ppg` remains unresolved.** Opening now has a separate 4× indexed route (below), using the external `title.bin` palette.
- **Player select objects** load from `hd-cg/ef02_usa.hdcg` (or `ef02.hdcg` on
  a Japanese build); the engine grows cache 13 for them alongside the two
  player caches.
- **The HUD is not replaced**, and cannot be by this route. The health bars,
  timer, super gauge, portraits and names come from `scrscrn.ppg`, which the
  engine draws by selecting a palette bank per call. `direct32` bakes a single
  palette into the pixels, so replacing it corrupts every HUD colour.
  `build_screens.py` refuses it, along with `Warning`, `Warninge`, `CapLogo`
  and `Ake3rd`, for the same reason. Menus also live in the untouched
  `efNN.bin` groups. `docs/ui-asset-survey.md` has the inventory.
- **UI screen seams.** Each screen chunk is upscaled independently because each
  is its own texture. On the visibly tiled screens (`Select`, `Win*`, `Rank*`)
  check the edges.
- **Not lossless art.** Colour is quantised into the original 64-colour banks,
  hidden tile regions keep stock pixels, and where several frames share a tile
  the most-visible occurrence wins.
- **Visual review is incomplete.** Vulkan and OpenGL smoke tests confirm HD
  uploads for both players; they are not a full pass over every animation,
  costume and stage, and no performance profiling has been done.

---

## Licence and attribution

The tools and documentation here are the authors'. *Street Fighter III: 3rd
Strike* and all its assets are © Capcom; this project is unaffiliated with
Capcom and distributes none of their work. 3SX is a separate project under its
own licence. Upscayl and the Real-ESRGAN-derived models are third-party
projects under their own licences.

## Opening at 4×

Opening decodes all 90 chunks using `title.bin`. Preserve its per-rectangle
palette banks with the HD indexed route:

```sh
python3 tools/export_for_upscale.py Opening
python3 tools/upscale.py Opening --scale 4
python3 tools/ppg_repack.py SF33RD.AFS --stages Opening --src work/upscaled --mode indexed-hd
python3 tools/verify_indexed_hd.py SF33RD.AFS Opening work/repacked/Opening.ppg
python3 tools/build_hd_test.py
```

This requires the updated engine patch. `indexed-hd` stores 8-bit indices and
retains the original transfer table. The indexed-only `formARGB` marker
`0x480N` specifies density N (2–4); the renderer divides the texture grid by N
when computing rectangle geometry and UVs. The texture itself remains full
resolution. Stock engines do not understand this extension. `build_screens.py`
still rejects direct32 conversion for Opening. Palette quantization limits
new colours; playback still needs visual review for seams and temporal shimmer.
