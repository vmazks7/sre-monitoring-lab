#!/usr/bin/env bash
# Создаёт toxiproxy-прокси для лабы. Запускать один раз ПОСЛЕ старта toxiproxy
# (systemd unit toxiproxy.service, порт API — 8474).
#
# Провайдеры мокаются локально на AppServer (порты 9101-9103),
# Toxiproxy слушает 10101-10103 и форвардит на них. 
# К БД AppServer тоже ходит ЧЕРЕЗ прокси (15432 -> DBHost:5432) — так вся деградация управляется в одном месте.
set -e

API="http://localhost:8474"
DBHOST="10.10.10.12"   # поменять, если IP DBHost другой

create_proxy() {
  local name=$1 listen=$2 upstream=$3
  echo "Создаю прокси: $name ($listen -> $upstream)"
  curl -s -X POST "$API/proxies" \
    -H "Content-Type: application/json" \
    -d "{\"name\":\"$name\",\"listen\":\"$listen\",\"upstream\":\"$upstream\",\"enabled\":true}" \
    || echo "  (возможно уже существует — ок)"
  echo
}

create_proxy paycore     0.0.0.0:10101 127.0.0.1:9101
create_proxy fastpay     0.0.0.0:10102 127.0.0.1:9102
create_proxy cryptogate  0.0.0.0:10103 127.0.0.1:9103
create_proxy postgres    0.0.0.0:15432 "${DBHOST}:5432"

echo "Готово. Проверка:"
curl -s "$API/proxies" | python3 -m json.tool
