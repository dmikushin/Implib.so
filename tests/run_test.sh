#!/bin/sh
test_filename=$1
log_filename=$2
ref_filename=$3
$test_filename >$log_filename
diff $log_filename $ref_filename
