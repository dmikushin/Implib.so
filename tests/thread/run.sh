#!/bin/sh

# Copyright 2025 Yury Gribov
#
# The MIT License (MIT)
# 
# Use of this source code is governed by MIT license that can be
# found in the LICENSE.txt file.

# This is a test for checking thread safety of Implib's shims.
#
# There is also a separate driver for Deterministic Simulation Testing
# in run_unthread.sh.

set -eu

cd $(dirname $0)

if test -n "${1:-}"; then
  ARCH="$1"
fi

. ../common.sh

CFLAGS="-g -O2 $CFLAGS"
N=10

# Build shlib to test against
$CC $CFLAGS -shared -fPIC interposed.c -o libinterposed.so

# Prepare implib
${PYTHON:-} ../../implib-gen.py -q --target $TARGET libinterposed.so

# Test without export shims

$CC $CFLAGS -fPIE main.c libinterposed.so.tramp.S libinterposed.so.init.c $LIBS

for i in $(seq 1 $N); do
  LD_LIBRARY_PATH=.:${LD_LIBRARY_PATH:-} $INTERP ./a.out > a.out.log
  diff test.ref a.out.log
done

# Test with export shims

$CC $CFLAGS -DIMPLIB_EXPORT_SHIMS -fPIE main.c libinterposed.so.tramp.S libinterposed.so.init.c $LIBS

for i in $(seq 1 $N); do
  LD_LIBRARY_PATH=.:${LD_LIBRARY_PATH:-} $INTERP ./a.out > a.out.log
  diff test.ref a.out.log
done

# Test with Tsan

if test -n "$TSAN_AVAILABLE"; then
  # ASLR keeps breaking Tsan mmaps
  if test $(cat /proc/sys/kernel/randomize_va_space) != 0; then
    if sudo -n true 2>/dev/null; then
      echo 0 | sudo tee /proc/sys/kernel/randomize_va_space
    else
      echo "Warning: Cannot disable ASLR without sudo, skipping ThreadSanitizer test"
      echo SUCCESS
      exit 0
    fi
  fi

  $CC $CFLAGS -g -fsanitize=thread -fPIE main.c libinterposed.so.tramp.S libinterposed.so.init.c $LIBS

  for i in $(seq 1 $N); do
    LD_LIBRARY_PATH=.:${LD_LIBRARY_PATH:-} $INTERP ./a.out > a.out.log
    diff test.ref a.out.log
  done
fi

echo SUCCESS
