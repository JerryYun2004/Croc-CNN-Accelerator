#ifndef CROC_CNN_DRIVER_H
#define CROC_CNN_DRIVER_H
#include <stdint.h>
#include <stddef.h>
#define CNN_CTRL_BASE ((uintptr_t)0x20001000u)
#define CNN_BUFFER_BASE ((uintptr_t)0x20002000u)
#define CNN_IMAGE_BYTES 784u
#define CNN_MODEL_BYTES 1596u
#define CNN_OK 0
#define CNN_BUSY (-1)
#define CNN_ERROR (-2)
#define CNN_TIMEOUT (-3)
#define CNN_ARGUMENT (-4)
#ifndef CNN_MMIO_READ32
#define CNN_MMIO_READ32(a) (*(volatile uint32_t *)(uintptr_t)(a))
#endif
#ifndef CNN_MMIO_WRITE32
#define CNN_MMIO_WRITE32(a,v) (*(volatile uint32_t *)(uintptr_t)(a) = (uint32_t)(v))
#endif
static inline void cnn_fence(void) {
#if defined(__riscv)
  __asm__ volatile ("fence iorw, iorw" ::: "memory");
#else
  __asm__ volatile ("" ::: "memory");
#endif
}
static inline uint32_t cnn_status(void) { return CNN_MMIO_READ32(CNN_CTRL_BASE + 12u); }
static inline uint32_t cnn_error_code(void) { return CNN_MMIO_READ32(CNN_CTRL_BASE + 16u); }
static inline int cnn_clear(void) {
  if (cnn_status() & 1u) return CNN_BUSY;
  CNN_MMIO_WRITE32(CNN_CTRL_BASE + 8u, 2u); cnn_fence(); return CNN_OK;
}
static inline void cnn_copy_words(uintptr_t destination, const uint8_t *source, size_t bytes) {
  for (size_t i=0;i<bytes;i+=4) {
    uint32_t v=(uint32_t)source[i] | ((uint32_t)source[i+1]<<8) |
               ((uint32_t)source[i+2]<<16) | ((uint32_t)source[i+3]<<24);
    CNN_MMIO_WRITE32(destination+i, v);
  }
  cnn_fence();
}
static inline int cnn_load_model(const uint8_t *model, unsigned shift) {
  if (!model || shift > 30u) return CNN_ARGUMENT;
  if (cnn_status() & 1u) return CNN_BUSY;
  cnn_copy_words(CNN_BUFFER_BASE+0x310u, model, CNN_MODEL_BYTES);
  CNN_MMIO_WRITE32(CNN_CTRL_BASE+24u, shift); cnn_fence(); return CNN_OK;
}
static inline int cnn_load_image(const uint8_t *image) {
  if (!image) return CNN_ARGUMENT;
  if (cnn_status() & 1u) return CNN_BUSY;
  cnn_copy_words(CNN_BUFFER_BASE, image, CNN_IMAGE_BYTES); return CNN_OK;
}
static inline int cnn_start(void) {
  if (cnn_status() & 1u) return CNN_BUSY;
  cnn_fence(); CNN_MMIO_WRITE32(CNN_CTRL_BASE+8u, 1u); cnn_fence(); return CNN_OK;
}
// poll_limit bounds software polling, not an OBI access that never responds.
// TIMEOUT/ERROR do not abort hardware. Check BUSY before modifying the buffer.
static inline int cnn_wait(uint32_t poll_limit, unsigned *prediction, uint32_t *cycles) {
  while (poll_limit--) {
    uint32_t status=cnn_status();
    if (status & 4u) return CNN_ERROR;
    if ((status & 3u) == 2u) {
      cnn_fence();
      if (prediction) *prediction=CNN_MMIO_READ32(CNN_CTRL_BASE+28u);
      if (cycles) *cycles=CNN_MMIO_READ32(CNN_CTRL_BASE+20u);
      return CNN_OK;
    }
  }
  return CNN_TIMEOUT;
}
#endif
