#include <stdint.h>
#include <limits.h>
#include "reference.h"
int main(void) {
    const int32_t inputs[] = {-5,-4,-3,-1,0,1,3,4,5};
    const int32_t expected[] = {-3,-2,-2,-1,0,1,2,2,3};
    for (unsigned i = 0; i < sizeof inputs / sizeof inputs[0]; ++i)
        if (cnn_round_shift(inputs[i],1) != expected[i]) return 1;
    if (cnn_round_shift(INT32_MIN,0) != INT32_MIN) return 2;
    if (cnn_round_shift(INT32_MIN,30) != -2) return 3;
    if (cnn_round_shift(INT32_MAX,30) != 2) return 4;
    if (cnn_round_shift(-1,2) != 0) return 5;
    int32_t scores[10] = {INT32_MIN,-1,-1,-2,-3,-4,-5,-6,-7,-8};
    if (cnn_argmax(scores) != 1) return 6;
    return 0;
}
