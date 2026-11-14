#ifndef CROC_CNN_REFERENCE_H
#define CROC_CNN_REFERENCE_H
#include <stdint.h>
int32_t cnn_round_shift(int32_t value, unsigned shift);
void cnn_infer(const uint8_t image[784], uint8_t pooled[144], int32_t scores[10]);
unsigned cnn_argmax(const int32_t scores[10]);
#endif
