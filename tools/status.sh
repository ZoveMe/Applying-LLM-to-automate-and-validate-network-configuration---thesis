#!/usr/bin/env bash
# Што е активно во моментов: Ollama, Docker, лабораториите и веб-интерфејсот.
# Ништо не се менува — само се чита состојбата.
#
# Извршување:  bash tools/status.sh

echo "=============================================================="
echo " СОСТОЈБА НА ПРОЕКТОТ — $(date '+%Y-%m-%d %H:%M')"
echo "=============================================================="

# ---------------------------------------------------------------- Ollama
echo
echo "── Ollama (порта 11434) ──"
if curl -s --max-time 3 http://localhost:11434/api/tags >/dev/null 2>&1; then
  n=$(curl -s http://localhost:11434/api/tags \
      | grep -o '"name"' | wc -l)
  echo "  работи · достапни модели: $n"
  echo "  вчитани во меморија:"
  curl -s http://localhost:11434/api/ps \
    | grep -o '"name":"[^"]*"' | cut -d'"' -f4 | sed 's/^/    /' \
    || echo "    (ниту еден)"
else
  echo "  не работи   → стартувај со:  ollama serve"
fi

# ---------------------------------------------------------------- Docker
echo
echo "── Docker ──"
if docker info >/dev/null 2>&1; then
  total=$(docker ps -q | wc -l)
  echo "  работи · активни контејнери: $total"
else
  echo "  не работи или нема дозвола   → стартувај Docker Desktop"
fi

# --------------------------------------------------------- лаборатории
echo
echo "── Лаборатории (Containerlab) ──"
for prefix in "clab-thesis-net-" "clab-thesis-net-xl-"; do
  n=$(docker ps --filter "name=${prefix}" --format '{{.Names}}' 2>/dev/null | wc -l)
  if [ "$n" -gt 0 ]; then
    echo "  ${prefix}*  →  $n јазли"
    docker ps --filter "name=${prefix}" \
      --format '    {{.Names}}   {{.Status}}' 2>/dev/null
  else
    echo "  ${prefix}*  →  не работи"
  fi
done

# ---------------------------------------------------------------- порти
echo
echo "── Порти што слушаат ──"
check_port () {   # $1 = порта, $2 = опис
  if command -v ss >/dev/null 2>&1; then
    ss -ltn 2>/dev/null | grep -q ":$1 " && echo "  $1  зафатена   ($2)" && return
  elif command -v netstat >/dev/null 2>&1; then
    netstat -ltn 2>/dev/null | grep -q ":$1 " && echo "  $1  зафатена   ($2)" && return
  fi
  echo "  $1  слободна    ($2)"
}
check_port 11434 "Ollama"
check_port 50080 "clab graph"
for p in $(seq 8901 8910); do check_port "$p" "веб-интерфејс"; done

# ------------------------------------------------- процеси на проектот
echo
echo "── Процеси на проектот ──"
found=$(pgrep -af "live_llm_dashboard|live_freeform|live_counterfactual|run_explain" 2>/dev/null)
if [ -n "$found" ]; then
  echo "$found" | sed 's/^/  /'
else
  echo "  нема активни скрипти од проектот"
fi

echo
echo "=============================================================="
