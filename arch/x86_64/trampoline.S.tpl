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
  .cfi_def_cfa_offset 8  // Return address
  // Intel opt. manual says to
  // "make the fall-through code following a conditional branch be the likely target for a branch with a forward target"
  // to hint static predictor.
  cmpq $$0, _${lib_suffix}_tramp_table+$offset(%rip)
  je 2f
1:
  jmp *_${lib_suffix}_tramp_table+$offset(%rip)
2:
  pushq $$$number
  .cfi_adjust_cfa_offset 8
  call _${lib_suffix}_save_regs_and_resolve
  addq $$8, %rsp
  .cfi_adjust_cfa_offset -8
  jmp *%rax
  .cfi_endproc

