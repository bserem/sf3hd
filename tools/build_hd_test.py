#!/usr/bin/env python3
"""Build the local-only v1 bundle: stages and characters exclusively.

Run from the repository root. Requires the local AFS, extracted character
frames, work/repacked/*.ppg and the configured 3sx/build-test build directory.
Everything it writes stays under work/release-v1/ and is local-only.
"""
import argparse
import shutil
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cg_decode_tile import TEXGROUP
from decode_stages import STAGES


def run(*args):
    subprocess.run([str(a) for a in args], check=True)


def release_replacements(root):
    """Explicit allowlist: unrelated repacks can never enter the release."""
    paths = [root / name for _, name, _, _ in STAGES]
    missing = [p.name for p in paths if not p.is_file()]
    if missing:
        raise SystemExit('Missing release stages: ' + ', '.join(missing))
    return sorted(paths)


def check_sidecar_directory(directory):
    allowed = {Path(cg).stem + '.hdcg' for cg in TEXGROUP}
    extras = {p.name for p in directory.iterdir()} - allowed if directory.exists() else set()
    if extras:
        raise SystemExit('Release sidecar directory contains non-character files: ' +
                         ', '.join(sorted(extras)) + '. Preserve them elsewhere before rebuilding.')


def reuse_packs(source, destination):
    """Copy only character packs whose hashes match a previous bundle manifest."""
    manifest = json.loads((source / 'manifest.json').read_text())
    for cg in sorted(TEXGROUP):
        name = Path(cg).stem + '.hdcg'
        rel = 'hd-cg/' + name
        src = source / rel
        with src.open('rb') as f:
            digest = hashlib.file_digest(f, 'sha256').hexdigest()
        if manifest['sha256'].get(rel) != digest:
            raise SystemExit('Character pack hash mismatch: ' + str(src))
        shutil.copy2(src, destination / name)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--reuse-bundle', type=Path, help='reuse character packs verified against this bundle manifest')
    args = ap.parse_args()
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    bundle = root / 'work/release-v1'
    profile = bundle / 'user-data/CrowdedStreet/3SX'
    resources = profile / 'resources'
    resources.mkdir(parents=True, exist_ok=True)
    replacements = release_replacements(Path('work/repacked'))
    check_sidecar_directory(bundle / 'hd-cg')
    # The archive's own character set is the authority; pl15 does not exist.
    characters = [cg.split('.')[0] for cg in sorted(TEXGROUP)]
    (bundle / 'hd-cg').mkdir(parents=True, exist_ok=True)
    if args.reuse_bundle:
        reuse_packs(args.reuse_bundle, bundle / 'hd-cg')
    else:
        run(sys.executable, 'tools/upscale.py', *characters)
        run(sys.executable, 'tools/cut_pages.py', *characters)
        run(sys.executable, 'tools/check_upscaled.py', *characters)
        for cg in characters:
            run(sys.executable, 'tools/cg_hd_pack.py', 'SF33RD.AFS', '--cg', cg + '.bin',
                '--out', bundle / 'hd-cg' / (cg + '.hdcg'))
    archive = resources / 'SF33RD.AFS'
    run(sys.executable, 'tools/afs_rebuild.py', 'SF33RD.AFS', archive,
        *[arg for p in replacements for arg in ('--replace', p.name + '=' + str(p))])
    run(sys.executable, 'tools/verify_rebuild.py', 'SF33RD.AFS', archive,
        *[p.name + '=' + str(p) for p in replacements])
    run('cmake', '--build', '3sx/build-test', '--parallel', '6')
    run('cmake', '--install', '3sx/build-test', '--prefix', bundle / 'application')
    # A separate profile keeps the user's installed archive and preferences intact.
    if not (profile / 'config').exists():
        (profile / 'config').write_text('fullscreen = false\nwindow-width = 1536\nwindow-height = 896\n'
                                        'scale-mode = nearest\nscanlines = 0\narcade-balance = false\n')
    launcher = bundle / 'run.sh'
    launcher.write_text('''#!/bin/sh
set -eu
bundle_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
export XDG_DATA_HOME="$bundle_dir/user-data"
export THREESX_HD_CG="$bundle_dir/hd-cg"
if [ "${1:-}" = "--stock" ]; then
    unset THREESX_HD_CG
    shift
fi
cd "$bundle_dir/application/bin"
exec ./3sx "$@"
''')
    launcher.chmod(0o755)
    (bundle / 'README.txt').write_text('''Release v1: stages and characters only

Run ./run.sh and play normally; every character archive uses 4x textures.
Run ./run.sh --stock to compare original character textures on the same HD
stages. --stock only affects characters: stage replacements
live in the archive itself and are always active.
Default test window: 1536x896. Preferences and saves are in ./user-data/.
The character sidecars are loaded from ./hd-cg/.
The pack retains the original palettes. Hidden/shared tile regions can differ
from the composited upscale. Visual QA comparisons are in ../hd-cg/qa/.
This bundle contains your local game archive and must remain local-only.

Rebuild from the repository root: python3 tools/build_hd_test.py
''')
    hashes = {}
    for p in (archive, bundle / 'application/bin/3sx', *sorted((bundle / 'hd-cg').glob('*.hdcg'))):
        with p.open('rb') as f:
            hashes[str(p.relative_to(bundle))] = hashlib.file_digest(f, 'sha256').hexdigest()
    (bundle / 'manifest.json').write_text(json.dumps(
        {'release_scope': ['stages', 'characters'], 'characters': characters, 'density': 4,
         'replaced_entries': sorted(p.name for p in replacements),
         'sha256': hashes}, indent=2) + '\n')
    print('Ready:', launcher)


if __name__ == '__main__':
    main()
