#!/usr/bin/env bash
# Собира состојба од enterprise лабораторијата за да се утврди зошто
# рутирањето или политиката не работат. Ништо не менува.
#
# Извршување:  bash tools/diagnose-ent.sh
P="clab-thesis-net-ent"

hr () { echo; echo "══════ $* ══════"; }

hr "1. Дали контејнерите работат"
docker ps -a --filter "name=$P" --format '  {{.Names}}  {{.State}}  {{.Status}}'

hr "2. Дали FRR ги подигнал демоните на core1"
docker exec "$P-core1" sh -c 'ps -o pid,comm 2>/dev/null | grep -E "zebra|ospfd|staticd" || ps aux | grep -E "zebra|ospfd" | grep -v grep' \
  || echo "  (не може да се прочита списокот процеси)"

hr "3. Дали датотеката daemons стигнала во контејнерот"
docker exec "$P-core1" sh -c 'grep -E "^(zebra|ospfd|staticd)=" /etc/frr/daemons' \
  || echo "  ГРЕШКА: /etc/frr/daemons не е читлива"

hr "4. Дали frr.conf е вчитана"
docker exec "$P-core1" sh -c 'ls -l /etc/frr/frr.conf; head -3 /etc/frr/frr.conf' \
  || echo "  ГРЕШКА: /etc/frr/frr.conf не е читлива"

hr "5. Што мисли самиот FRR дека му е конфигурацијата"
docker exec "$P-core1" vtysh -c "show running-config" 2>&1 | head -30 \
  || echo "  ГРЕШКА: vtysh не одговара"

hr "6. Адреси на интерфејсите на core1"
docker exec "$P-core1" ip -brief addr 2>/dev/null

hr "7. OSPF соседи на core1"
docker exec "$P-core1" vtysh -c "show ip ospf neighbor" 2>&1 | head -15

hr "8. Рутирачка табела на core1"
docker exec "$P-core1" vtysh -c "show ip route" 2>&1 | head -20

hr "9. Адреси на edge-hq (втор рутер, за споредба)"
docker exec "$P-edge-hq" ip -brief addr 2>/dev/null

hr "10. Правила за филтрирање"
for n in fw-dmz edge-hq; do
  echo "  --- $n ---"
  docker exec "$P-$n" iptables -S FORWARD 2>&1 | sed 's/^/    /'
done

hr "11. Адреса и премин на pc-hq1"
docker exec "$P-pc-hq1" sh -c 'ip -brief addr; echo "--- рути ---"; ip route' 2>/dev/null

echo
echo "══════ крај ══════"
