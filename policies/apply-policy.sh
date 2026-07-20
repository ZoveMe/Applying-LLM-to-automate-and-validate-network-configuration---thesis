#!/usr/bin/env bash
# Apply the INTENDED access policy on r1:
#   clients (10.0.1.0/24) may NOT reach the management network (10.0.99.0/24).
#   Everything else stays permitted.
# This is the policy your validation layer (Week 3) will check automatically.
set -e
R1="clab-thesis-net-r1"

# Make sure iptables exists inside the router container (Alpine-based image).
docker exec "$R1" sh -c 'command -v iptables >/dev/null 2>&1 || apk add --no-cache iptables >/dev/null 2>&1 || true'

# Insert the deny rule on the forwarding path (idempotent: only add if missing).
if docker exec "$R1" iptables -C FORWARD -s 10.0.1.0/24 -d 10.0.99.0/24 -j DROP 2>/dev/null; then
  echo "Policy already present on r1."
else
  docker exec "$R1" iptables -I FORWARD -s 10.0.1.0/24 -d 10.0.99.0/24 -j DROP
  echo "Applied: client -> management DENY on r1."
fi

echo "--- r1 FORWARD chain ---"
docker exec "$R1" iptables -S FORWARD
