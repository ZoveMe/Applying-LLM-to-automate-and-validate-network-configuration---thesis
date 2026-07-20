#!/usr/bin/env bash
# Remove the access policy from r1.
# Useful for fault-injection experiments: with the rule gone, the management
# network becomes reachable from clients, and your validation should FLAG it.
R1="clab-thesis-net-r1"

if docker exec "$R1" iptables -D FORWARD -s 10.0.1.0/24 -d 10.0.99.0/24 -j DROP 2>/dev/null; then
  echo "Removed: client -> management DENY on r1."
else
  echo "Rule not present (nothing to remove)."
fi
