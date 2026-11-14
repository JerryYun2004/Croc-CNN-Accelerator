#include <stdint.h>
#include "cnn_driver.h"

/* The loader supplies one accepted fixture in firmware SRAM, never CNN SRAM. */
struct fixture {
  uint8_t image[784];
  uint8_t model[1596];
  uint8_t pooled[144];
  int32_t scores[10];
  uint8_t prediction, shift;
  uint8_t pad[2];
};
extern const volatile struct fixture input;
struct measurement {
  uint32_t model_copy, image_copy, start_wait, verify, total, engine, prediction, magic;
};
volatile struct measurement report[2] __attribute__((section(".report")));
extern uint32_t __stack_bottom[];

static inline uint32_t cycles(void) {
  uint32_t n;
  __asm__ volatile ("csrr %0, mcycle" : "=r"(n) :: "memory");
  return n;
}

void croc_exception_handler(void) {
  *(volatile uint32_t *)0x03000008 = 0xeeee0001;
  for (;;) __asm__ volatile ("wfi");
}

int main(void) {
  __asm__ volatile ("csrw mcountinhibit, zero" ::: "memory");
  if (CNN_MMIO_READ32(CNN_CTRL_BASE) != 0x434e4e31u ||
      CNN_MMIO_READ32(CNN_CTRL_BASE+4) != 0x00010000u) return 1;
  for (unsigned run=0; run<2; ++run) {
    volatile struct measurement *r=&report[run];
    uint32_t begin=cycles(), t=begin;
    if (!run && cnn_load_model((const uint8_t *)input.model,input.shift)) return 2;
    uint32_t next=cycles(); r->model_copy=next-t; t=next;
    if (cnn_load_image((const uint8_t *)input.image)) return 3;
    next=cycles(); r->image_copy=next-t; t=next;
    unsigned prediction; uint32_t engine;
    if (cnn_start() || cnn_wait(100000u,&prediction,&engine)) return 4;
    next=cycles(); r->start_wait=next-t; t=next;
    r->engine=engine; r->prediction=prediction;
    if (prediction!=input.prediction || engine!=302451u) return 5;
    for (unsigned i=0;i<36;++i)
      if (CNN_MMIO_READ32(CNN_BUFFER_BASE+0x950+4*i) !=
          ((const volatile uint32_t *)input.pooled)[i]) return 6;
    for (unsigned i=0;i<10;++i)
      if ((int32_t)CNN_MMIO_READ32(CNN_BUFFER_BASE+0x9e0+4*i)!=input.scores[i]) return 7;
    for (unsigned i=0;i<4;++i) if (__stack_bottom[i]!=0xa5a5a5a5u) return 8;
    next=cycles(); r->verify=next-t; r->total=next-begin;
    cnn_fence(); r->magic=0x43503243u;
  }
  return 0;
}
