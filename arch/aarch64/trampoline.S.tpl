/*
 * Copyright 2018-2025 Yury Gribov
 *
 * The MIT License (MIT)
 *
 * Use of this source code is governed by MIT license that can be
 * found in the LICENSE.txt file.
 */

  .globl $sym
  .p2align 4
#ifndef __APPLE__
  .type $sym, %function
#endif
#ifndef IMPLIB_EXPORT_SHIMS
#ifndef __APPLE__
  .hidden $sym
#else
  .private_extern $sym
#endif
#endif
$sym:
  .cfi_startproc

1:
  // Load address
  // TODO: can we do this faster on newer ARMs?
  adrp ip0, _${lib_suffix}_tramp_table+$offset
  ldr ip0, [ip0, #:lo12:_${lib_suffix}_tramp_table+$offset]
 
  cbz ip0, 2f

  // Fast path
  br ip0

2:
  // Slow path
  mov ip0, $number & 0xffff
#if $number > 0xffff
  movk ip0, $number >> 16, lsl #16
#endif
  stp ip0, lr, [sp, #-16]!; .cfi_adjust_cfa_offset 16; .cfi_rel_offset lr, 8
  bl _${lib_suffix}_save_regs_and_resolve
  ldp xzr, lr, [sp], #16; .cfi_adjust_cfa_offset -16; .cfi_restore lr
  br ip0
  .cfi_endproc

