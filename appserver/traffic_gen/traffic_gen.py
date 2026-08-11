#!/usr/bin/env python3
"""
traffic_gen.py — генератор трафика платёжного шлюза с интерактивным меню.

Архитектура:
  генератор -> (pending в БД) -> settlement-поток -> HTTP-вызов провайдера
  через Toxiproxy-прокси -> circuit breaker фиксирует результат -> статус
  транзакции обновляется, пишется provider_responses.

Все "аварии" включаются из меню без перезапуска процесса:
  - латентность/таймауты/полный отказ провайдера — через Toxiproxy
  - латентность/отказ БД — через Toxiproxy
  - принудительное размыкание circuit breaker
  - зависшие pending (не резолвятся вообще)
  - расхождение статусов с провайдером (reconciliation-кейс)
  - долгая блокировка в БД
  - всплеск нагрузки

Зависимости:
    pip install psycopg2-binary faker prometheus_client requests

Запуск:
    python3 traffic_gen.py seed     # один раз, если users/providers/games пустые
    python3 traffic_gen.py run
"""
import argparse
import json
import logging
import os
import random
import sys
import threading
import time
from datetime import datetime, timedelta

import psycopg2
import requests
from faker import Faker
from prometheus_client import Counter, Gauge, Histogram, start_http_server

from config import (
    DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD,
    PROVIDERS, LOG_PATH, METRICS_PORT,
    MIN_INTERVAL_SEC, MAX_INTERVAL_SEC,
    CB_WINDOW_SIZE, CB_FAIL_THRESHOLD, CB_COOLDOWN_SEC, CB_HALF_OPEN_PROBES,
)
from circuit_breaker import ProviderRouter, State
from toxiproxy_control import degrade_provider, restore_provider, degrade_db, restore_db

fake = Faker("ru_RU")

DB_CONN = dict(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD)

TX_TYPES = ["deposit", "withdrawal"]
ERROR_CODES_SETTLE = {
    "declined": "DO_NOT_HONOUR",
    "timeout": "TIMEOUT",
    "connection_error": "PROVIDER_UNAVAILABLE",
    "http_error": "PROVIDER_ERROR",
}

# ──────────────────────────────────────────────────────────────────────────
# Логирование
# ──────────────────────────────────────────────────────────────────────────

logger = logging.getLogger("traffic_gen")
logger.setLevel(logging.INFO)


class JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {"timestamp": datetime.utcnow().isoformat() + "Z", "level": record.levelname}
        if isinstance(record.msg, dict):
            payload.update(record.msg)
        else:
            payload["message"] = record.getMessage()
        return json.dumps(payload, ensure_ascii=False)


def setup_logging():
    handlers = [logging.StreamHandler(sys.stdout)]
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        handlers.append(logging.FileHandler(LOG_PATH))
    except (PermissionError, OSError):
        pass
    for h in handlers:
        h.setFormatter(JsonFormatter())
        logger.addHandler(h)


# ──────────────────────────────────────────────────────────────────────────
# Метрики
# ──────────────────────────────────────────────────────────────────────────

TX_TOTAL = Counter("app_transactions_total", "Всего созданных транзакций", ["type"])
TX_STATUS_TOTAL = Counter("app_transaction_status_total", "Транзакции по финальному статусу", ["status", "type"])
TX_LATENCY = Histogram(
    "app_transaction_settle_seconds", "Время от pending до финального статуса",
    buckets=(0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600, 1800, 3600, 7200),
)
PENDING_GAUGE = Gauge("app_pending_transactions", "Текущее число транзакций в статусе pending")
PROVIDER_MISMATCH_TOTAL = Counter("app_provider_mismatch_total", "Расхождения статуса с провайдером")
AML_PATTERN_TOTAL = Counter("app_aml_pattern_total", "Сгенерированные AML-паттерны")
PROVIDER_CONVERSION_GAUGE = Gauge("app_provider_conversion_rate", "Конверсия провайдера (0-1)", ["provider"])
PROVIDER_CB_STATE = Gauge("app_provider_circuit_state", "0=closed 1=half_open 2=open", ["provider"])
PROVIDER_FAILOVER_TOTAL = Counter("app_provider_failover_total", "Переключений трафика между провайдерами")
GENERATION_RUNNING_GAUGE = Gauge("app_generation_running", "1, если генерация включена")

CB_STATE_VALUE = {State.CLOSED: 0, State.HALF_OPEN: 1, State.OPEN: 2}


# ──────────────────────────────────────────────────────────────────────────
# Общее состояние, управляемое из меню
# ──────────────────────────────────────────────────────────────────────────

class SharedState:
    def __init__(self):
        self.running = True
        self.stuck_provider = None       # provider_id, чьи pending перестали резолвиться
        self.mismatch_active = False     # усиленный поток расхождений с провайдером
        self.spike_requested = False


# ──────────────────────────────────────────────────────────────────────────
# ID-счётчики 
# ──────────────────────────────────────────────────────────────────────────

class IdCounter:
    def __init__(self, conn, table, pk):
        cur = conn.cursor()
        cur.execute(f"SELECT COALESCE(MAX({pk}), 0) FROM {table};")
        self._next = cur.fetchone()[0] + 1
        self._lock = threading.Lock()

    def next(self):
        with self._lock:
            v = self._next
            self._next += 1
            return v


# ──────────────────────────────────────────────────────────────────────────
# Сиды (users / providers / games)
# ──────────────────────────────────────────────────────────────────────────

def seed(conn, n_users=200):
    cur = conn.cursor()

    cur.execute("SELECT count(*) FROM users;")
    if cur.fetchone()[0] == 0:
        uid_counter = IdCounter(conn, "users", "user_id")
        for _ in range(n_users):
            cur.execute(
                "INSERT INTO users (user_id, email, country, created_at) VALUES (%s,%s,%s,%s);",
                (uid_counter.next(), fake.email(), random.choice(["RU", "US", "DE", "KZ"]),
                 datetime.now() - timedelta(days=random.randint(1, 180))),
            )
        print(f"добавлено {n_users} пользователей")
    else:
        print("users уже наполнены, пропускаю")

    cur.execute("SELECT count(*) FROM providers;")
    if cur.fetchone()[0] == 0:
        cur.execute("""
            INSERT INTO providers (provider_id, provider_name, contact_email) VALUES
            (1, 'PayCore', 'support@paycore.io'),
            (2, 'FastPay', 'ops@fastpay.com'),
            (3, 'CryptoGate', 'help@cryptogate.net');
        """)
        print("добавлены providers")
    else:
        print("providers уже наполнены, пропускаю")

    cur.execute("SELECT count(*) FROM games;")
    if cur.fetchone()[0] == 0:
        cur.execute("""
            INSERT INTO games (game_id, game_name, provider_id) VALUES
            (1, 'Lucky Slots', 1), (2, 'Poker Stars', 1),
            (3, 'Roulette Live', 2), (4, 'Blackjack Pro', 2),
            (5, 'Dice King', 3);
        """)
        print("добавлены games")
    else:
        print("games уже наполнены, пропускаю")

    conn.commit()


# ──────────────────────────────────────────────────────────────────────────
# Вызов провайдера через Toxiproxy
# ──────────────────────────────────────────────────────────────────────────

def call_provider(provider_id: int, amount: float):
    """Возвращает (success: bool, reason: str)."""
    port = PROVIDERS[provider_id]["proxy_port"]
    url = f"http://127.0.0.1:{port}/charge"
    try:
        resp = requests.post(url, json={"amount": amount}, timeout=6)
        if resp.status_code == 200:
            status = resp.json().get("status")
            return status == "success", ("ok" if status == "success" else "declined")
        return False, "http_error"
    except requests.exceptions.Timeout:
        return False, "timeout"
    except requests.exceptions.ConnectionError:
        return False, "connection_error"


# ──────────────────────────────────────────────────────────────────────────
# Создание новой транзакции (в статусе pending)
# ──────────────────────────────────────────────────────────────────────────

def create_transaction(conn, ids, user_ids, provider_ids):
    cur = conn.cursor()
    tx_id = ids["tx"].next()
    user_id = random.choice(user_ids)
    provider_id = random.choice(provider_ids)
    tx_type = random.choices(TX_TYPES, weights=[0.7, 0.3])[0]
    amount = round(random.uniform(50, 3000), 2)
    now = datetime.now()

    cur.execute(
        "INSERT INTO transactions (transaction_id, user_id, amount, status, type, "
        "provider_id, error_code, created_at) VALUES (%s,%s,%s,'pending',%s,%s,NULL,%s);",
        (tx_id, user_id, amount, tx_type, provider_id, now),
    )
    conn.commit()

    TX_TOTAL.labels(type=tx_type).inc()
    logger.info({
        "event": "transaction_created", "transaction_id": tx_id, "user_id": user_id,
        "amount": amount, "type": tx_type, "provider_id": provider_id, "status": "pending",
    })

    if random.random() < 0.01:  # дубль
        dup_id = ids["tx"].next()
        cur.execute(
            "INSERT INTO transactions (transaction_id, user_id, amount, status, type, "
            "provider_id, error_code, created_at) VALUES (%s,%s,%s,'pending',%s,%s,NULL,%s);",
            (dup_id, user_id, amount, tx_type, provider_id, now + timedelta(seconds=random.randint(10, 240))),
        )
        conn.commit()
        logger.warning({"event": "duplicate_transaction", "original_id": tx_id, "duplicate_id": dup_id})


# ──────────────────────────────────────────────────────────────────────────
# Settlement — резолвит pending, реально дёргая провайдера через Toxiproxy
# ──────────────────────────────────────────────────────────────────────────

def settle_pending(conn, ids, router: ProviderRouter, state: SharedState):
    while True:
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT transaction_id, user_id, type, amount, provider_id, created_at "
                "FROM transactions WHERE status='pending' ORDER BY created_at LIMIT 50;"
            )
            rows = cur.fetchall()
            PENDING_GAUGE.set(len(rows))

            for tx_id, user_id, tx_type, amount, provider_id, created_at in rows:
                age = (datetime.now() - created_at).total_seconds()
                if age < 1:
                    continue  # даём транзакции немного "пожить" в pending

                if state.stuck_provider == provider_id:
                    continue  # сценарий "зависшие pending" — сознательно не резолвим

                effective_provider = router.pick_provider(preferred=provider_id)
                if effective_provider != provider_id:
                    cur.execute(
                        "UPDATE transactions SET provider_id=%s WHERE transaction_id=%s;",
                        (effective_provider, tx_id),
                    )
                    conn.commit()
                    PROVIDER_FAILOVER_TOTAL.inc()
                    logger.warning({
                        "event": "provider_failover", "transaction_id": tx_id,
                        "from_provider": provider_id, "to_provider": effective_provider,
                    })

                success, reason = call_provider(effective_provider, float(amount))
                router.report(effective_provider, success)

                status = "success" if success else ("timeout" if reason in ("timeout", "connection_error") else "failed")
                error_code = None if success else ERROR_CODES_SETTLE.get(reason, "UNKNOWN")

                cur.execute(
                    "UPDATE transactions SET status=%s, error_code=%s WHERE transaction_id=%s;",
                    (status, error_code, tx_id),
                )
                conn.commit()

                TX_STATUS_TOTAL.labels(status=status, type=tx_type).inc()
                TX_LATENCY.observe(age)

                cb = router.breakers[effective_provider]
                PROVIDER_CONVERSION_GAUGE.labels(provider=PROVIDERS[effective_provider]["name"]).set(cb.conversion_rate())
                PROVIDER_CB_STATE.labels(provider=PROVIDERS[effective_provider]["name"]).set(CB_STATE_VALUE[cb.state])

                logger.info({
                    "event": "transaction_settled", "transaction_id": tx_id, "status": status,
                    "provider_id": effective_provider, "settle_latency_sec": round(age, 1),
                })

                write_provider_response(conn, ids, tx_id, status, state)

                if tx_type == "deposit" and float(amount) > 2000 and status == "success" and random.random() < 0.15:
                    spawn_aml_withdrawal(conn, ids, user_id, effective_provider, float(amount))

        except Exception as e:
            logger.warning({"event": "settle_error", "error": str(e)})

        time.sleep(1)


def write_provider_response(conn, ids, tx_id, our_status, state: SharedState):
    cur = conn.cursor()
    resp_id = ids["resp"].next()

    mismatch_rate = 0.25 if state.mismatch_active else 0.02
    if random.random() < mismatch_rate and our_status in ("success", "failed"):
        external_status = "failed" if our_status == "success" else "success"
        PROVIDER_MISMATCH_TOTAL.inc()
        logger.warning({"event": "provider_status_mismatch", "transaction_id": tx_id,
                         "our_status": our_status, "external_status": external_status})
    else:
        external_status = our_status if our_status in ("success", "failed") else "success"

    cur.execute(
        "INSERT INTO provider_responses (response_id, transaction_id, external_status, responded_at) "
        "VALUES (%s,%s,%s,%s);",
        (resp_id, tx_id, external_status, datetime.now()),
    )
    conn.commit()


def spawn_aml_withdrawal(conn, ids, user_id, provider_id, deposit_amount):
    cur = conn.cursor()
    tx_id = ids["tx"].next()
    withdraw_amount = round(deposit_amount * random.uniform(0.85, 0.98), 2)
    cur.execute(
        "INSERT INTO transactions (transaction_id, user_id, amount, status, type, "
        "provider_id, error_code, created_at) VALUES (%s,%s,%s,'success',%s,%s,NULL,%s);",
        (tx_id, user_id, withdraw_amount, "withdrawal", provider_id, datetime.now()),
    )
    conn.commit()
    AML_PATTERN_TOTAL.inc()
    resp_id = ids["resp"].next()
    cur.execute(
        "INSERT INTO provider_responses (response_id, transaction_id, external_status, responded_at) "
        "VALUES (%s,%s,'success',%s);",
        (resp_id, tx_id, datetime.now()),
    )
    conn.commit()
    logger.warning({"event": "aml_pattern", "user_id": user_id, "withdrawal_tx_id": tx_id, "amount": withdraw_amount})


def simulate_db_lock(duration_sec=120):
    def _hold():
        try:
            conn = psycopg2.connect(**DB_CONN)
            cur = conn.cursor()
            cur.execute("SELECT transaction_id FROM transactions ORDER BY random() LIMIT 1 FOR UPDATE;")
            row = cur.fetchone()
            logger.warning({"event": "db_lock_started", "transaction_id": row[0] if row else None,
                             "duration_sec": duration_sec})
            time.sleep(duration_sec)
            conn.commit()
            conn.close()
            logger.warning({"event": "db_lock_released"})
        except Exception as e:
            logger.warning({"event": "db_lock_error", "error": str(e)})
    threading.Thread(target=_hold, daemon=True).start()


# ──────────────────────────────────────────────────────────────────────────
# Фоновая игровая активность (для реалистичности данных)
# ──────────────────────────────────────────────────────────────────────────

def spawn_sessions(conn, ids, user_ids, game_ids):
    while True:
        try:
            cur = conn.cursor()
            sid = ids["session"].next()
            cur.execute(
                "INSERT INTO sessions (session_id, user_id, game_id, started_at, ended_at) "
                "VALUES (%s,%s,%s,%s,%s);",
                (sid, random.choice(user_ids), random.choice(game_ids),
                 datetime.now() - timedelta(minutes=random.randint(0, 5)),
                 datetime.now() + timedelta(minutes=random.randint(5, 90))),
            )
            conn.commit()
        except Exception as e:
            logger.warning({"event": "session_error", "error": str(e)})
        time.sleep(random.uniform(3, 10))


# ──────────────────────────────────────────────────────────────────────────
# Генерация — интервал случайный 1-10 сек
# ──────────────────────────────────────────────────────────────────────────

def generation_loop(conn, ids, user_ids, provider_ids, state: SharedState):
    while True:
        GENERATION_RUNNING_GAUGE.set(1 if state.running else 0)

        if not state.running:
            time.sleep(1)
            continue

        if state.spike_requested:
            burst = random.randint(15, 40)
            logger.info({"event": "traffic_spike", "burst_size": burst})
            for _ in range(burst):
                create_transaction(conn, ids, user_ids, provider_ids)
            state.spike_requested = False
            continue

        create_transaction(conn, ids, user_ids, provider_ids)
        time.sleep(random.uniform(MIN_INTERVAL_SEC, MAX_INTERVAL_SEC))


# ──────────────────────────────────────────────────────────────────────────
# Интерактивное меню
# ──────────────────────────────────────────────────────────────────────────

def pick_provider_input():
    print("Провайдер:  1) PayCore   2) FastPay   3) CryptoGate")
    raw = input("> ").strip()
    try:
        pid = int(raw)
        if pid in PROVIDERS:
            return pid
    except ValueError:
        pass
    print("Некорректный ввод, беру PayCore по умолчанию")
    return 1


def print_status(state: SharedState, router: ProviderRouter):
    print(f"\nГенерация: {'ВКЛ' if state.running else 'ВЫКЛ'}")
    for pid, info in PROVIDERS.items():
        cb = router.breakers[pid]
        print(f"  {info['name']:<10} state={cb.state.value:<10} conversion={cb.conversion_rate():.0%}")
    stuck = PROVIDERS[state.stuck_provider]['name'] if state.stuck_provider else "нет"
    print(f"Зависшие pending: {stuck}")
    print(f"Mismatch: {'включен' if state.mismatch_active else 'выключен'}\n")


def degradation_menu(state: SharedState, router: ProviderRouter):
    while True:
        print("""
=== Сценарии деградации ===
 1. Латентность у провайдера (Toxiproxy)
 2. Таймауты у провайдера (Toxiproxy)
 3. Полный отказ провайдера (Toxiproxy outage)
 4. Узкий канал у провайдера (bandwidth limit)
 5. Принудительное размыкание circuit breaker
 6. Зависшие pending-транзакции (не резолвятся вообще)
 7. Расхождение статусов с провайдером (mismatch)
 8. Латентность до БД (Toxiproxy)
 9. Полная недоступность БД (Toxiproxy outage)
10. Долгая блокировка в БД (held lock, 120 сек)
11. Всплеск нагрузки (разово)
12. Снять ВСЕ деградации (reset)
13. Назад
""")
        choice = input("> ").strip()
        if choice == "1":
            pid = pick_provider_input(); degrade_provider(pid, "latency")
            print(f"Латентность включена для {PROVIDERS[pid]['name']}")
        elif choice == "2":
            pid = pick_provider_input(); degrade_provider(pid, "timeout")
            print(f"Таймауты включены для {PROVIDERS[pid]['name']}")
        elif choice == "3":
            pid = pick_provider_input(); degrade_provider(pid, "outage")
            print(f"{PROVIDERS[pid]['name']} полностью недоступен")
        elif choice == "4":
            pid = pick_provider_input(); degrade_provider(pid, "slow")
            print(f"Узкий канал включен для {PROVIDERS[pid]['name']}")
        elif choice == "5":
            pid = pick_provider_input(); router.breakers[pid].force_open()
            print(f"Circuit breaker {PROVIDERS[pid]['name']} принудительно OPEN")
        elif choice == "6":
            pid = pick_provider_input(); state.stuck_provider = pid
            print(f"Pending-транзакции {PROVIDERS[pid]['name']} перестали резолвиться")
        elif choice == "7":
            state.mismatch_active = True
            print("Расхождение статусов с провайдером включено (25% транзакций)")
        elif choice == "8":
            degrade_db("latency"); print("Латентность до БД включена")
        elif choice == "9":
            degrade_db("outage"); print("БД полностью недоступна")
        elif choice == "10":
            simulate_db_lock(120); print("Запущена блокировка на 120 сек")
        elif choice == "11":
            state.spike_requested = True; print("Всплеск будет сгенерирован в ближайшую итерацию")
        elif choice == "12":
            for pid in PROVIDERS:
                restore_provider(pid)
            restore_db()
            state.stuck_provider = None
            state.mismatch_active = False
            print("Все деградации сняты")
        elif choice == "13":
            return
        else:
            print("Не понял, попробуй ещё раз")


def menu_loop(state: SharedState, router: ProviderRouter):
    while True:
        print("""
=== Управление генератором трафика ===
1. Включить генерацию
2. Выключить генерацию
3. Сценарии деградации
4. Статус
5. Выход
""")
        choice = input("> ").strip()
        if choice == "1":
            state.running = True; print("Генерация включена")
        elif choice == "2":
            state.running = False; print("Генерация выключена")
        elif choice == "3":
            degradation_menu(state, router)
        elif choice == "4":
            print_status(state, router)
        elif choice == "5":
            print("Выход. Фоновые потоки останавливаются вместе с процессом.")
            os._exit(0)
        else:
            print("Не понял, попробуй ещё раз")


# ──────────────────────────────────────────────────────────────────────────
# Запуск
# ──────────────────────────────────────────────────────────────────────────

def run(args):
    setup_logging()
    conn = psycopg2.connect(**DB_CONN)

    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users;")
    user_ids = [r[0] for r in cur.fetchall()]
    cur.execute("SELECT provider_id FROM providers;")
    provider_ids = [r[0] for r in cur.fetchall()]
    cur.execute("SELECT game_id FROM games;")
    game_ids = [r[0] for r in cur.fetchall()]

    if not user_ids or not provider_ids:
        print("ОШИБКА: users/providers пусты — сначала запусти: traffic_gen.py seed")
        sys.exit(1)

    ids = {
        "tx": IdCounter(conn, "transactions", "transaction_id"),
        "resp": IdCounter(conn, "provider_responses", "response_id"),
        "session": IdCounter(conn, "sessions", "session_id"),
    }

    state = SharedState()
    router = ProviderRouter(
        provider_ids, window_size=CB_WINDOW_SIZE, fail_threshold=CB_FAIL_THRESHOLD,
        cooldown_sec=CB_COOLDOWN_SEC, half_open_probes=CB_HALF_OPEN_PROBES,
    )

    start_http_server(METRICS_PORT)
    print(f"Метрики: :{METRICS_PORT}/metrics")

    settle_conn = psycopg2.connect(**DB_CONN)
    threading.Thread(target=settle_pending, args=(settle_conn, ids, router, state), daemon=True).start()

    gen_conn = psycopg2.connect(**DB_CONN)
    threading.Thread(target=generation_loop, args=(gen_conn, ids, user_ids, provider_ids, state), daemon=True).start()

    if not args.no_sessions:
        session_conn = psycopg2.connect(**DB_CONN)
        threading.Thread(target=spawn_sessions, args=(session_conn, ids, user_ids, game_ids), daemon=True).start()

    menu_loop(state, router)  # блокирует основной поток


def main():
    parser = argparse.ArgumentParser(description="Генератор трафика платёжного шлюза")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("seed", help="наполнить users/providers/games, если пусто")
    run_p = sub.add_parser("run", help="запустить генератор с интерактивным меню")
    run_p.add_argument("--no-sessions", action="store_true", help="не генерировать фоновую игровую активность")

    args = parser.parse_args()
    conn = psycopg2.connect(**DB_CONN)
    if args.command == "seed":
        seed(conn); conn.close()
    elif args.command == "run":
        conn.close(); run(args)


if __name__ == "__main__":
    main()
