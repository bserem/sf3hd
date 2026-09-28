#!/bin/sh
# Check and set up everything the sf3-hd pipeline needs.
#
# This script installs TOOLS only. It never downloads, contains or produces
# game assets: you supply your own SF33RD.AFS. See README.md.
#
#   ./setup.sh          check the environment and report what is missing
#   ./setup.sh --venv   also create .venv and install the Python dependencies
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$root"
missing=0
note() { printf '  %s\n' "$*"; }
ok()   { printf 'OK   %-22s %s\n' "$1" "$2"; }
bad()  { printf 'MISS %-22s %s\n' "$1" "$2"; missing=$((missing+1)); }

# ---------------------------------------------------------------- python ----
if command -v python3 >/dev/null 2>&1; then
    ok python3 "$(python3 -V 2>&1)"
else
    bad python3 "install python3 (3.8 or newer)"
fi

if [ "${1:-}" = "--venv" ]; then
    [ -d .venv ] || python3 -m venv .venv
    ./.venv/bin/pip install --quiet --upgrade pip
    ./.venv/bin/pip install --quiet -r requirements.txt
    ok venv ".venv ready; activate with '. .venv/bin/activate'"
elif python3 -c "import PIL" >/dev/null 2>&1; then
    ok Pillow "$(python3 -c 'import PIL;print(PIL.__version__)')"
else
    bad Pillow "run ./setup.sh --venv, or pip install -r requirements.txt"
fi

# ------------------------------------------------------------ build tools ----
for tool in cmake git; do
    if command -v $tool >/dev/null 2>&1; then
        ok $tool "$(command -v $tool)"
    else
        bad $tool "required to build 3SX"
    fi
done
if command -v cc >/dev/null 2>&1 || command -v gcc >/dev/null 2>&1; then
    ok "c compiler" "$(command -v cc || command -v gcc)"
else
    bad "c compiler" "install gcc or clang"
fi

# --------------------------------------------------------------- upscaler ----
# upscayl-bin is the ncnn/Vulkan CLI from Upscayl. It is a separate project
# with its own licence; we neither bundle nor redistribute it.
bin=${UPSCAYL_BIN:-}
if [ -z "$bin" ]; then
    for cand in "$(command -v upscayl-bin 2>/dev/null || true)" \
                "$HOME/Programs/bin/upscayl-bin" \
                /opt/Upscayl/resources/bin/upscayl-bin \
                /usr/lib/upscayl/resources/bin/upscayl-bin; do
        [ -n "$cand" ] && [ -x "$cand" ] && bin=$cand && break
    done
fi
if [ -n "$bin" ] && [ -x "$bin" ]; then
    ok upscayl-bin "$bin"
else
    bad upscayl-bin "install Upscayl (https://github.com/upscayl/upscayl) or set UPSCAYL_BIN"
fi

models=${UPSCAYL_MODELS:-$HOME/Downloads/custom-models-main/models}
if [ -f "$models/realesr-animevideov3-x4.param" ] && \
   [ -f "$models/realesr-animevideov3-x4.bin" ]; then
    ok model "realesr-animevideov3-x4 in $models"
else
    bad model "realesr-animevideov3-x4.{param,bin} not in $models"
    note "from https://github.com/upscayl/custom-models (set UPSCAYL_MODELS)"
fi

# ---------------------------------------------------------------- sources ----
if [ -d 3sx/.git ]; then
    ok 3sx "$(git -C 3sx rev-parse --short HEAD) $(git -C 3sx log -1 --format=%s)"
    if [ -f 3sx/src/sf33rd/Source/Game/rendering/hd_cg.c ]; then
        note "engine patch: already applied"
    else
        note "apply the engine patch: (cd 3sx && git apply --3way ../patches/3sx-hd-textures.patch)"
    fi
else
    bad 3sx "git clone https://github.com/crowded-street/3sx.git 3sx"
fi

# Your own legally-obtained archive. Never committed, never distributed.
if [ -f SF33RD.AFS ]; then
    size=$(wc -c < SF33RD.AFS)
    if [ "$size" -eq 642492416 ]; then
        ok SF33RD.AFS "$size bytes"
    else
        ok SF33RD.AFS "$size bytes (expected 642492416; a different region/release may still work)"
    fi
else
    bad SF33RD.AFS "copy your own SF33RD.AFS to $root/SF33RD.AFS (see README.md)"
fi

echo
if [ "$missing" -eq 0 ]; then
    echo "Ready. Next: see README.md, 'Build it'."
else
    echo "$missing item(s) missing; see the notes above."
    exit 1
fi
