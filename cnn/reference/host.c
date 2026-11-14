#include <stdio.h>
#include "reference.h"
int main(void) {
    uint8_t image[784], pool[144]; int32_t scores[10];
    for (;;) {
        size_t n = fread(image, 1, sizeof image, stdin);
        if (n == 0) return ferror(stdin) ? 2 : 0;
        if (n != sizeof image) return 3;
        cnn_infer(image, pool, scores);
        if (fwrite(pool, 1, sizeof pool, stdout) != sizeof pool) return 4;
        for (unsigned c = 0; c < 10; ++c) {
            uint32_t v = (uint32_t)scores[c];
            for (unsigned b = 0; b < 4; ++b) if (putchar((int)((v >> (8*b)) & 255u)) == EOF) return 4;
        }
        if (putchar((int)cnn_argmax(scores)) == EOF) return 4;
    }
}
