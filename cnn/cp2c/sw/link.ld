OUTPUT_ARCH("riscv")
ENTRY(_start)
MEMORY { SRAM (rwx) : ORIGIN = 0x10000000, LENGTH = 4096 }
SECTIONS {
  .vectors : { KEEP(*(.vectors)) } > SRAM
  .text : { *(.text._start) *(.text .text.*) *(.rodata .rodata.*) } > SRAM
  .data : ALIGN(4) { *(.sdata .sdata.* .data .data.*) } > SRAM
  .fixture : ALIGN(4) { KEEP(*(.fixture)) } > SRAM
  .report (NOLOAD) : ALIGN(4) { *(.report) } > SRAM
  .bss (NOLOAD) : ALIGN(4) { *(.bss .bss.* .sbss .sbss.*) *(COMMON) } > SRAM
  __used_end = .;
  __stack_bottom = ORIGIN(SRAM) + LENGTH(SRAM) - 256;
  __stack_pointer$ = ORIGIN(SRAM) + LENGTH(SRAM);
  __global_pointer$ = ORIGIN(SRAM) + 2048;
  ASSERT(__used_end <= __stack_bottom, "CP2C firmware/data exceeds SRAM minus 256-byte stack")
  /DISCARD/ : { *(.comment .eh_frame .riscv.attributes) }
}
