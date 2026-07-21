#!/usr/bin/env bash
# Automated fault-injection experiment with fail-safe recovery.

set -uo pipefail
cd "$(dirname "$0")/.."

R1="clab-thesis-net-r1"
EV="docs/evidence"
VAL=(python3 validation/dynamic_validate.py)
ACL=(-s 10.0.1.0/24 -d 10.0.99.0/24 -j DROP)
PREFIX="10.0.2.0/24"
NEXT_HOP="10.0.12.2"
mkdir -p "$EV"

CLEANUP_NEEDED=0

container_is_running() {
    [ "$(docker inspect -f '{{.State.Running}}' "$R1" 2>/dev/null)" = "true" ]
}

remove_all_acl_rules() {
    local rc

    while true; do
        docker exec "$R1" iptables -C FORWARD \
            "${ACL[@]}" >/dev/null 2>&1
        rc=$?

        case "$rc" in
            0)
                docker exec "$R1" iptables -D FORWARD \
                    "${ACL[@]}" >/dev/null 2>&1 || return 1
                ;;
            1)
                return 0
                ;;
            *)
                return 1
                ;;
        esac
    done
}

restore_acl() {
    remove_all_acl_rules || return 1

    docker exec "$R1" iptables -I FORWARD \
        "${ACL[@]}" >/dev/null 2>&1
}

remove_test_routes() {
    docker exec "$R1" vtysh \
        -c "configure terminal" \
        -c "no ip route $PREFIX $NEXT_HOP" \
        -c "no ip route $PREFIX blackhole" \
        >/dev/null 2>&1
}

restore_route() {
    remove_test_routes || return 1

    docker exec "$R1" vtysh \
        -c "configure terminal" \
        -c "ip route $PREFIX $NEXT_HOP" \
        >/dev/null 2>&1
}

inject_blackhole() {
    remove_test_routes || return 1

    docker exec "$R1" vtysh \
        -c "configure terminal" \
        -c "ip route $PREFIX blackhole" \
        >/dev/null 2>&1
}

restore_baseline() {
    local failed=0

    restore_acl || failed=1
    restore_route || failed=1

    return "$failed"
}

cleanup() {
    local status=$?
    trap - EXIT INT TERM

    if [ "$CLEANUP_NEEDED" -eq 1 ]; then
        echo ""
        echo "Restoring intended ACL and route before exit..."

        if container_is_running && restore_baseline; then
            echo "Recovery cleanup: OK"
        else
            echo "CRITICAL: automatic recovery failed; inspect r1 before reuse." >&2
            status=2
        fi
    fi

    exit "$status"
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

require_success() {
    local description="$1"
    shift

    if ! "$@"; then
        echo "ERROR: $description failed; stopping the experiment." >&2
        exit 2
    fi
}

step() {
    local num="$1"
    local expect="$2"
    local file="$3"
    local desc="$4"
    local got
    local word

    echo ""
    echo "=== Step $num: $desc ==="

    "${VAL[@]}" --out "$EV/$file"
    got=$?

    if [ "$got" -ne 0 ] && [ "$got" -ne 1 ]; then
        echo "ERROR: validator infrastructure failure in step $num (exit=$got)." >&2
        exit 2
    fi

    if [ "$got" -ne "$expect" ]; then
        echo "UNEXPECTED: expected exit=$expect but got exit=$got -> $EV/$file" >&2
        exit 1
    fi

    word="MATCHES_INTENT"
    if [ "$expect" -eq 1 ]; then
        word="DOES_NOT_MATCH_INTENT (fault detected)"
    fi

    echo "OK: expected and got $word -> $EV/$file"
}

echo "Fault-injection harness — thesis-net"
echo "Evidence directory: $EV"

if ! container_is_running; then
    echo "ERROR: $R1 is not running. Deploy the lab first." >&2
    exit 2
fi

CLEANUP_NEEDED=1

step 1 0 "20-dynamic-baseline.json" \
    "baseline (correct configuration)"

require_success "ACL fault injection" remove_all_acl_rules
step 2 1 "21-dynamic-fault-acl-removed.json" \
    "fault injected: deny policy removed from r1"

require_success "ACL recovery" restore_acl
step 3 0 "22-dynamic-recovery-acl-restored.json" \
    "recovery: deny policy restored on r1"

require_success "missing-route fault injection" remove_test_routes
sleep 1
step 4 1 "23-dynamic-fault-route-missing.json" \
    "fault injected: r1 route to $PREFIX removed"

require_success "route recovery" restore_route
sleep 1
step 5 0 "24-dynamic-recovery-route-restored.json" \
    "recovery: r1 route to $PREFIX restored"

require_success "blackhole-route fault injection" inject_blackhole
sleep 1
step 6 1 "25-dynamic-fault-route-blackhole.json" \
    "fault injected: r1 route to $PREFIX blackholed"

require_success "final route recovery" restore_route
sleep 1
step 7 0 "26-dynamic-recovery-final.json" \
    "recovery: correct route restored (final state)"

CLEANUP_NEEDED=0

echo ""
echo "============================================================"
echo "HARNESS RESULT: PASS — every fault was detected and every recovery verified."
echo "Evidence saved in $EV (20-dynamic-*.json through 26-dynamic-*.json)."