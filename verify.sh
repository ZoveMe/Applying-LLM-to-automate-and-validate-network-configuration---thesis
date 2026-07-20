#!/usr/bin/env bash
# Reachability check — a first, manual preview of the deterministic validation
# layer you will automate in Week 3.
#
# Intended policy:
#   client -> server      ALLOWED   (web access)
#   client -> management  DENIED    (after apply-policy.sh)
#   server -> client      ALLOWED   (return path)
#
# It compares the ACTUAL reachability against the EXPECTED policy and reports
# PASS / FAIL per check, then an overall verdict.

LAB="clab-thesis-net"
pass=0
fail=0

check() {
  local desc="$1" expected="$2" src="$3" dst="$4"
  local result
  if docker exec "$src" ping -c1 -W2 "$dst" >/dev/null 2>&1; then
    result="reachable"
  else
    result="unreachable"
  fi
  if [ "$result" = "$expected" ]; then
    printf "PASS | %-32s expected=%-11s got=%s\n" "$desc" "$expected" "$result"
    pass=$((pass + 1))
  else
    printf "FAIL | %-32s expected=%-11s got=%s\n" "$desc" "$expected" "$result"
    fail=$((fail + 1))
  fi
}

echo "=== Validating network state against intended policy ==="
check "client -> server (web)"        reachable   "${LAB}-h-client" 10.0.2.10
check "client -> management (denied)" unreachable "${LAB}-h-client" 10.0.99.10
check "server -> client (return)"     reachable   "${LAB}-h-server" 10.0.1.10
echo "-------------------------------------------------------"
echo "Passed: ${pass}   Failed: ${fail}"
if [ "$fail" -eq 0 ]; then
  echo "RESULT: network MATCHES the intended policy."
  exit 0
else
  echo "RESULT: network does NOT match the intended policy."
  exit 1
fi
