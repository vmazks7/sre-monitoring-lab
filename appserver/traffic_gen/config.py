import os

# БД — подключаемся ЧЕРЕЗ toxiproxy-прокси на самом AppServer (127.0.0.1:15432), а не напрямую в DBHost.
# Так деградация БД (модуль "сценарии") включается на прокси и не требует правок в коде генератора.
DB_HOST = os.environ.get("DB_HOST", "127.0.0.1")
DB_PORT = int(os.environ.get("DB_PORT", "15432"))
DB_NAME = os.environ.get("DB_NAME", "shop")
DB_USER = os.environ.get("DB_USER", "appuser")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "CHANGE_ME_set_via_env_or_.env_file")

TOXIPROXY_API = os.environ.get("TOXIPROXY_API", "http://localhost:8474")

PROVIDERS = {
    1: {"name": "PayCore",    "mock_port": 9101, "proxy_name": "paycore",    "proxy_port": 10101},
    2: {"name": "FastPay",    "mock_port": 9102, "proxy_name": "fastpay",    "proxy_port": 10102},
    3: {"name": "CryptoGate", "mock_port": 9103, "proxy_name": "cryptogate", "proxy_port": 10103},
}

LOG_PATH = os.environ.get("LOG_PATH", "/var/log/appserver/transactions.log")
METRICS_PORT = int(os.environ.get("METRICS_PORT", "8000"))

MIN_INTERVAL_SEC = float(os.environ.get("MIN_INTERVAL_SEC", "1"))
MAX_INTERVAL_SEC = float(os.environ.get("MAX_INTERVAL_SEC", "10"))

CB_WINDOW_SIZE = int(os.environ.get("CB_WINDOW_SIZE", "20"))
CB_FAIL_THRESHOLD = float(os.environ.get("CB_FAIL_THRESHOLD", "0.4"))
CB_COOLDOWN_SEC = int(os.environ.get("CB_COOLDOWN_SEC", "30"))
CB_HALF_OPEN_PROBES = int(os.environ.get("CB_HALF_OPEN_PROBES", "5"))
