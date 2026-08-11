"""
Обёртка над Toxiproxy HTTP API (порт 8474 по умолчанию).
Toxics API: https://github.com/Shopify/toxiproxy

Прокси должны быть заранее созданы через toxiproxy/toxiproxy_setup.sh:
  paycore, fastpay, cryptogate, postgres
"""
import requests

from config import TOXIPROXY_API, PROVIDERS


def _proxy_name_for(provider_id: int) -> str:
    return PROVIDERS[provider_id]["proxy_name"]


def _post(path, json_body):
    try:
        return requests.post(f"{TOXIPROXY_API}{path}", json=json_body, timeout=3)
    except requests.exceptions.RequestException as e:
        print(f"[toxiproxy] ошибка запроса {path}: {e}")
        return None


def apply_latency(proxy_name, latency_ms=3000, jitter_ms=1000, toxicity=1.0):
    _post(f"/proxies/{proxy_name}/toxics", {
        "name": "lag", "type": "latency", "toxicity": toxicity,
        "attributes": {"latency": latency_ms, "jitter": jitter_ms},
    })


def apply_timeout(proxy_name, timeout_ms=5000, toxicity=1.0):
    _post(f"/proxies/{proxy_name}/toxics", {
        "name": "timeout_toxic", "type": "timeout", "toxicity": toxicity,
        "attributes": {"timeout": timeout_ms},
    })


def apply_bandwidth_limit(proxy_name, rate_kb=2, toxicity=1.0):
    _post(f"/proxies/{proxy_name}/toxics", {
        "name": "slow_pipe", "type": "bandwidth", "toxicity": toxicity,
        "attributes": {"rate": rate_kb},
    })


def clear_toxics(proxy_name):
    try:
        resp = requests.get(f"{TOXIPROXY_API}/proxies/{proxy_name}/toxics", timeout=3)
        for toxic in resp.json():
            requests.delete(f"{TOXIPROXY_API}/proxies/{proxy_name}/toxics/{toxic['name']}", timeout=3)
    except requests.exceptions.RequestException:
        pass


def disable_proxy(proxy_name):
    _post(f"/proxies/{proxy_name}", {"enabled": False})


def enable_proxy(proxy_name):
    _post(f"/proxies/{proxy_name}", {"enabled": True})


def degrade_provider(provider_id: int, mode: str):
    name = _proxy_name_for(provider_id)
    clear_toxics(name)
    enable_proxy(name)
    if mode == "latency":
        apply_latency(name)
    elif mode == "timeout":
        apply_timeout(name)
    elif mode == "outage":
        disable_proxy(name)
    elif mode == "slow":
        apply_bandwidth_limit(name)


def restore_provider(provider_id: int):
    name = _proxy_name_for(provider_id)
    clear_toxics(name)
    enable_proxy(name)


def degrade_db(mode: str):
    clear_toxics("postgres")
    enable_proxy("postgres")
    if mode == "latency":
        apply_latency("postgres", latency_ms=1500, jitter_ms=500)
    elif mode == "outage":
        disable_proxy("postgres")


def restore_db():
    clear_toxics("postgres")
    enable_proxy("postgres")
