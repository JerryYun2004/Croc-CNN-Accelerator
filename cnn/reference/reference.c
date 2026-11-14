#include "reference.h"
#include "model_data.h"
int32_t cnn_round_shift(int32_t value, unsigned shift) {
    if (shift == 0u) return value;
    int64_t magnitude = value < 0 ? -(int64_t)value : (int64_t)value;
    magnitude = (magnitude + ((int64_t)1 << (shift - 1u))) >> shift;
    return (int32_t)(value < 0 ? -magnitude : magnitude);
}
void cnn_infer(const uint8_t image[784], uint8_t pooled[144], int32_t scores[10]) {
    for (unsigned py = 0; py < 6; ++py) {
        for (unsigned px = 0; px < 6; ++px) {
            for (unsigned oc = 0; oc < 4; ++oc) {
                int32_t maximum = 0;
                for (unsigned dy = 0; dy < 4; ++dy) {
                    for (unsigned dx = 0; dx < 4; ++dx) {
                        int32_t acc = conv_bias[oc];
                        for (unsigned ky = 0; ky < 5; ++ky) {
                            for (unsigned kx = 0; kx < 5; ++kx) {
                                unsigned i = (4*py+dy+ky)*28+4*px+dx+kx;
                                acc += (int32_t)(image[i] >> 1) * (int32_t)conv_weights[oc*25+ky*5+kx];
                            }
                        }
                        int32_t q = cnn_round_shift(acc, CONV_SHIFT);
                        if (q > 127) q = 127;
                        if (q > maximum) maximum = q;
                    }
                }
                pooled[(py*6+px)*4+oc] = (uint8_t)maximum;
            }
        }
    }
    for (unsigned c = 0; c < 10; ++c) {
        int32_t acc = fc_bias[c];
        for (unsigned i = 0; i < 144; ++i) acc += (int32_t)pooled[i] * (int32_t)fc_weights[c*144+i];
        scores[c] = acc;
    }
}
unsigned cnn_argmax(const int32_t scores[10]) {
    unsigned best = 0;
    for (unsigned c = 1; c < 10; ++c) if (scores[c] > scores[best]) best = c;
    return best;
}
