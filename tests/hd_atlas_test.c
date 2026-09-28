#include "sf33rd/Source/Game/rendering/hd_atlas.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>

int main(void) {
    unsigned cases = 0;
    for (unsigned density = 1; density <= 4; density *= 2)
    for (unsigned side = 8; side <= 32; side *= 2)
    for (unsigned bpp = 1; bpp <= 2; bpp++)
    for (unsigned source_density = 1; source_density <= density; source_density *= density > 1 ? density : 2) {
        unsigned width = 256 * density, columns = side == 32 ? 8 : 16;
        unsigned slot_side = side == 32 ? 32 : 16;
        size_t bytes = (size_t)width * width * bpp;
        uint8_t* memory = malloc(bytes + 32);
        uint8_t* expected = malloc(bytes);
        uint8_t src[128*128*2];
        for (unsigned i = 0; i < side * side * source_density * source_density * bpp; i++) src[i] = (i * 37 + i / 11) % 251;
        memset(memory, 0xFD, bytes + 32);
        memset(expected, 0xFD, bytes);
        for (unsigned slot = 0; slot < columns * columns; slot++) {
            assert(hdAtlasCopy(memory + 16, bytes, density, slot, side, bpp, src, source_density));
            for (unsigned y = 0; y < side * density; y++)
            for (unsigned x = 0; x < side * density; x++)
            for (unsigned channel = 0; channel < bpp; channel++) {
                unsigned px = (slot % columns) * slot_side * density + x;
                unsigned py = (slot / columns) * slot_side * density + y;
                unsigned sx = x * source_density / density, sy = y * source_density / density;
                expected[(py * width + px) * bpp + channel] = src[(sy * side * source_density + sx) * bpp + channel];
            }
            assert(memcmp(memory + 16, expected, bytes) == 0);
            for (unsigned i = 0; i < 16; i++) assert(memory[i] == 0xFD && memory[bytes+16+i] == 0xFD);
            cases++;
        }
        assert(!hdAtlasCopy(memory+16, bytes, density, columns*columns, side, bpp, src, source_density));
        assert(!hdAtlasCopy(memory+16, bytes-1, density, 0, side, bpp, src, source_density));
        free(memory); free(expected);
    }
    puts("HD atlas: stock/HD indexed/RGB, all slots and sentinels passed");
    printf("%u slot copies checked\n", cases);
    return 0;
}
