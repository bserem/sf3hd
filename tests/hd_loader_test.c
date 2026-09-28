// Exercises the HD sidecar loader against a fixture it writes itself.
//   hd_loader_test 4   loads a one-tile group 3 pack and expects HD uploads
//   hd_loader_test 1   runs with no pack and expects the stock path
#include "sf33rd/Source/Game/rendering/hd_cg.h"
#include <SDL3/SDL.h>
#include <assert.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>

static int fallback, linear;

void ppgRenewDotDataSeqs(Texture* t, u32 g, u32* p, u32 slot, u32 size) {
    (void)t; (void)g; (void)p; (void)slot; (void)size; fallback++;
}

void ppgRenewLinearDotDataSeqs(Texture* t, u32 g, const void* p, u32 slot, u32 side, u32 bpp, u32 density) {
    (void)t; (void)g;
    assert(slot == 255 && side == 16 && density == 4);
    if (bpp == 1) for (int i = 0; i < 4096; i++) assert(((const u8*)p)[i] == i % 64);
    else for (int i = 0; i < 4096; i++) assert(((const u16*)p)[i] == 0x8000 + i % 64);
    linear++;
}

// One group 3 tile: code 0, wh 1 (16px logical side), 4x, indices i%64.
static void write_fixture(const char* dir) {
    unsigned char pack[36 + 4096];
    memcpy(pack, "3SXCGHD1", 8);
    const unsigned header[] = { 4, 3, 1, sizeof pack };      // density, group, count, bytes
    const unsigned entry[] = { 1, 36, 4096 };                // wh, offset, payload bytes
    memcpy(pack + 8, header, sizeof header);
    memcpy(pack + 24, entry, sizeof entry);
    for (unsigned i = 0; i < 4096; i++) pack[36 + i] = i % 64;
    char path[512];
    SDL_snprintf(path, sizeof path, "%s/pl02.hdcg", dir);
    assert(SDL_SaveFile(path, pack, sizeof pack));
}

int main(int argc, char** argv) {
    assert(argc == 2);
    const int expected = atoi(argv[1]);
    char dir[512];
    if (expected == 4) {
        const char* tmp = SDL_getenv("TMPDIR");
        SDL_snprintf(dir, sizeof dir, "%s/hd_loader_test", tmp && *tmp ? tmp : "/tmp");
        assert(SDL_CreateDirectory(dir));
        write_fixture(dir);
        assert(SDL_setenv_unsafe("THREESX_HD_CG", dir, 1) == 0);
    }

    assert(hdCgScaleForCache(3) == expected);
    assert(hdCgScaleForCache(4) == expected);
    assert(hdCgScaleForCache(1) == 1);            // non-player caches never grow

    MultiTexture mt = { 0 };
    mt.id = 3;
    u8 stock[256] = { 0 };
    u16 palette[64];
    for (int i = 0; i < 64; i++) palette[i] = 0x8000 + i;

    hdCgUpload(&mt, 3, 0, 1, 40, stock, 255, 256, NULL);
    hdCgUpload(&mt, 3, 0, 1, 40, stock, 255, 512, palette);
    assert(linear == (expected == 4 ? 2 : 0));

    int previous = fallback;
    hdCgUpload(&mt, 2, 0, 1, 40, stock, 255, 256, NULL);   // group has no pack
    hdCgUpload(&mt, 3, 1, 1, 40, stock, 255, 256, NULL);   // tile beyond the pack
    hdCgUpload(&mt, 3, 0, 0, 40, stock, 255, 256, NULL);   // wh does not match
    // A 1x cache (the opening's melt uses cache 9) must not receive HD pixels.
    mt.id = 9;
    hdCgUpload(&mt, 3, 0, 1, 40, stock, 255, 256, NULL);
    assert(fallback == previous + 4);

    puts("HD loader: scale, payload, palette, cache density and fallback passed");
    return 0;
}
