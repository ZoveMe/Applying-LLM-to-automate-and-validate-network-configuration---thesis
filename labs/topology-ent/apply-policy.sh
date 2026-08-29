#!/usr/bin/env bash
# Ја применува пристапната политика на уредите што ја носат.
# Идемпотентна: правилото се внесува само ако го нема.
#
# ВАЖЕН Е РЕДОСЛЕДОТ. iptables ги чита правилата од горе надолу и
# застанува на првото што одговара. Затоа прво се внесуваат забраните,
# а дозволата за веќе воспоставени врски НА КРАЈ, на позиција 1 — за да
# заврши над сите нив. Ако се внесе прва, секоја наредна забрана ја
# турка надолу и таа престанува да важи.
set -e
P="clab-thesis-net-ent"

# корисниците немаат право директно до базата
N="$P-fw-dmz"
docker exec "$N" sh -c 'command -v iptables >/dev/null 2>&1 || apk add --no-cache iptables >/dev/null 2>&1 || true'
if docker exec "$N" iptables -C FORWARD -s 10.10.10.0/24 -d 10.30.30.12/32 -j DROP 2>/dev/null; then
  echo "fw-dmz: правилото 10.10.10.0/24 -> 10.30.30.12/32 веќе постои"
else
  docker exec "$N" iptables -I FORWARD -s 10.10.10.0/24 -d 10.30.30.12/32 -j DROP
  echo "fw-dmz: внесено 10.10.10.0/24 -> 10.30.30.12/32 DROP"
fi

# истото важи и за подружницата
N="$P-fw-dmz"
docker exec "$N" sh -c 'command -v iptables >/dev/null 2>&1 || apk add --no-cache iptables >/dev/null 2>&1 || true'
if docker exec "$N" iptables -C FORWARD -s 10.20.20.0/24 -d 10.30.30.12/32 -j DROP 2>/dev/null; then
  echo "fw-dmz: правилото 10.20.20.0/24 -> 10.30.30.12/32 веќе постои"
else
  docker exec "$N" iptables -I FORWARD -s 10.20.20.0/24 -d 10.30.30.12/32 -j DROP
  echo "fw-dmz: внесено 10.20.20.0/24 -> 10.30.30.12/32 DROP"
fi

# DMZ не смее да иницира кон внатрешната мрежа
N="$P-fw-dmz"
docker exec "$N" sh -c 'command -v iptables >/dev/null 2>&1 || apk add --no-cache iptables >/dev/null 2>&1 || true'
if docker exec "$N" iptables -C FORWARD -s 10.30.30.0/24 -d 10.10.10.0/24 -j DROP 2>/dev/null; then
  echo "fw-dmz: правилото 10.30.30.0/24 -> 10.10.10.0/24 веќе постои"
else
  docker exec "$N" iptables -I FORWARD -s 10.30.30.0/24 -d 10.10.10.0/24 -j DROP
  echo "fw-dmz: внесено 10.30.30.0/24 -> 10.10.10.0/24 DROP"
fi

# корисниците немаат пристап до управувачката мрежа
N="$P-edge-hq"
docker exec "$N" sh -c 'command -v iptables >/dev/null 2>&1 || apk add --no-cache iptables >/dev/null 2>&1 || true'
if docker exec "$N" iptables -C FORWARD -s 10.10.10.0/24 -d 10.10.99.0/24 -j DROP 2>/dev/null; then
  echo "edge-hq: правилото 10.10.10.0/24 -> 10.10.99.0/24 веќе постои"
else
  docker exec "$N" iptables -I FORWARD -s 10.10.10.0/24 -d 10.10.99.0/24 -j DROP
  echo "edge-hq: внесено 10.10.10.0/24 -> 10.10.99.0/24 DROP"
fi

# Дозволата за воспоставени врски оди последна, на позиција 1, за да
# застане над сите забрани внесени погоре. Без неа, забраната
# DMZ -> внатре го блокира и одговорот на сообраќај што корисникот
# сам го започнал, па дозволената насока престанува да работи.
for n in fw-dmz edge-hq; do
  N="$P-$n"
  if docker exec "$N" iptables -C FORWARD -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT 2>/dev/null; then
    docker exec "$N" iptables -D FORWARD -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
  fi
  docker exec "$N" iptables -I FORWARD 1 -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
  echo "$n: дозволата за воспоставени врски е поставена прва"
done

for n in fw-dmz edge-hq; do
  echo "--- $n FORWARD ---"
  docker exec "$P-$n" iptables -S FORWARD
done
