#include "../sw/cnn_driver.h"
// Compile-only freestanding C check. Firmware execution on CVE2 is CP2C.
int cnn_example(const uint8_t *model, const uint8_t *image) {
  int rc=cnn_load_model(model,9); if(rc) return rc;
  rc=cnn_load_image(image); if(rc) return rc;
  rc=cnn_start(); if(rc) return rc;
  return cnn_wait(1000000u,0,0);
}
