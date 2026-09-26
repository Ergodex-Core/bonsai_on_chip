#!/usr/bin/env bash
set -euo pipefail
if [[ $# != 1 ]]; then
  echo 'Usage: sudo bash validate.sh PREPARED_WORK_ROOT' >&2
  exit 2
fi
run=$(realpath -e "${1}")
[[ ${EUID} == 0 ]]
[[ -d ${run}/public-raw && -x ${run}/mmio_probe ]]
[[ ! -e ${run}/physical-started.txt ]]
cmp -s "${BASH_SOURCE[0]}" "${run}/harness/validate.sh"
(cd "${run}" && sha256sum -c build-sha256.txt) >"${run}/verified-build.log"
export LD_LIBRARY_PATH="${run}/aws-sdk-build/sdk/userspace/lib/so"
export PATH="${run}/bin:${PATH}"
exec 9>/var/lock/erg102-fpga-slot0.lock
flock -n 9 || {
  echo 'ABORT: slot advisory lock occupied'
  exit 73
}
test ! -e "${run}/RESERVATION.txt"
reservation_started=$(date -u +%FT%TZ)
printf 'ERG-102 official cl_axil_reg_access hardware smoke; pid=%s; since=%s\n' "$$" "${reservation_started}" >"${run}/RESERVATION.txt"
cleanup() {
  rc=$?
  printf '%s\n' "${rc}" >"${run}/physical-exitcode.txt"
  date -u +%FT%TZ >"${run}/physical-finished.txt"
  rm -f "${run}/RESERVATION.txt"
  flock -u 9
}
trap cleanup EXIT
capture() {
  local filename=$1 display=$2 rc
  shift 2
  printf 'COMMAND timeout 120 %s\n' "${display}" >"${run}/public-raw/${filename}.log"
  set +e
  timeout 120 "$@" >>"${run}/public-raw/${filename}.log" 2>&1
  rc=$?
  set -e
  printf 'EXIT_CODE %s\n' "${rc}" >>"${run}/public-raw/${filename}.log"
  printf '%s\t%s\n' "${filename}" "${rc}" >>"${run}/executions.tsv"
  return "${rc}"
}
check_idle() {
  ps -eo pid=,comm=,args= >"${run}/process-preflight.txt"
  local busy_rc=0
  pgrep -x 'vivado|xsim|bazel|test_sum|test_random|test_carry|mmio_probe|fpga-local-cmd|static-fpga-loca' >"${run}/competing-pids.txt" || busy_rc=$?
  if [[ ${busy_rc} == 0 ]]; then
    echo 'ABORT: competing build or FPGA process detected'
    exit 74
  fi
  [[ ${busy_rc} == 1 ]]
}
check_identity() {
  local log=$1
  grep -Eq '^AFI[[:space:]]+0[[:space:]]+agfi-06447dea0ca9b0a39[[:space:]]+loaded[[:space:]]+0[[:space:]]+ok[[:space:]]+0[[:space:]]+0x10212415' "${log}"
  grep -Eq '^AFIDEVICE[[:space:]]+0[[:space:]]+0x1d0f[[:space:]]+0xf006' "${log}"
}
date -u +%FT%TZ >"${run}/physical-started.txt"
check_idle
capture 00-before 'fpga-describe-local-image -S 0 -H' fpga-describe-local-image -S 0 -H
if ! grep -Eq '^AFI[[:space:]]+0[[:space:]]+No AFI[[:space:]]+cleared' "${run}/public-raw/00-before.log"; then
  # Reusing this exact public image is allowed; any other image aborts here.
  check_identity "${run}/public-raw/00-before.log"
  capture 00a-clear-expected 'fpga-clear-local-image -S 0 -H' fpga-clear-local-image -S 0 -H
fi
capture 01-load 'fpga-load-local-image -S 0 -I agfi-06447dea0ca9b0a39 -H' fpga-load-local-image -S 0 -I agfi-06447dea0ca9b0a39 -H
capture 02-identity 'fpga-describe-local-image -S 0 -H' fpga-describe-local-image -S 0 -H
check_identity "${run}/public-raw/02-identity.log"
capture 03-reset-initial 'mmio_probe reset' "${run}/mmio_probe" reset
capture 04-public-sum 'test_sum --slot 0 --a 10 --b 20' "${run}/software/runtime/test_sum" --slot 0 --a 10 --b 20
capture 05-public-carry 'test_carry' "${run}/software/runtime/test_carry"
capture 06-public-random 'test_random' "${run}/software/runtime/test_random"
for name in 04-public-sum 05-public-carry 06-public-random; do
  grep -q '^TEST PASSED$' "${run}/public-raw/${name}.log"
  if grep -Eq 'FAIL|ERROR|error:' "${run}/public-raw/${name}.log"; then exit 75; fi
done
carry_passes=$(grep -c '^PASS: ' "${run}/public-raw/05-public-carry.log")
test "${carry_passes}" -eq 1000
random_passes=$(grep -c '^PASS: ' "${run}/public-raw/06-public-random.log")
test "${random_passes}" -eq 1000
capture 07-probe 'mmio_probe full' "${run}/mmio_probe" full
check_idle
capture 08-before-reset 'fpga-describe-local-image -S 0 -H' fpga-describe-local-image -S 0 -H
check_identity "${run}/public-raw/08-before-reset.log"
capture 09-clear 'fpga-clear-local-image -S 0 -H' fpga-clear-local-image -S 0 -H
capture 10-reload 'fpga-load-local-image -S 0 -I agfi-06447dea0ca9b0a39 -H' fpga-load-local-image -S 0 -I agfi-06447dea0ca9b0a39 -H
capture 11-reset-reload 'mmio_probe reset' "${run}/mmio_probe" reset
capture 12-post-reset-sum 'test_sum --slot 0 --a 123 --b 456' "${run}/software/runtime/test_sum" --slot 0 --a 123 --b 456
grep -q '^TEST PASSED$' "${run}/public-raw/12-post-reset-sum.log"
capture 13-final 'fpga-describe-local-image -S 0 -H -M' fpga-describe-local-image -S 0 -H -M
check_identity "${run}/public-raw/13-final.log"
printf 'PHYSICAL_PASS\n' >"${run}/physical-result.txt"

cat "${run}/physical-result.txt"
cat "${run}/executions.tsv"
tail -n 2 "${run}/public-raw/07-probe.log"
