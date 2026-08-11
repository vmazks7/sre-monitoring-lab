#!/usr/bin/env python3
"""
Простейший мок платёжного провайдера — HTTP-эндпоинт /charge.
Запускается 3 раза (по одному на PayCore/FastPay/CryptoGate), каждый на своём порту.
Toxiproxy встаёт перед этим сервисом — сам мок ничего не знает о хаосе,
вся деградация (латентность/таймауты/outage) инжектится снаружи.

Запуск:
    python3 providers_mock.py 9101   # PayCore
    python3 providers_mock.py 9102   # FastPay
    python3 providers_mock.py 9103   # CryptoGate
"""
import random
import sys
import time

from fastapi import FastAPI
import uvicorn

app = FastAPI()

# базовый фоновый уровень сбоев именно на стороне провайдера (не связан с toxiproxy-хаосом) —
# имитирует то, что даже здоровый провайдер иногда отклоняет платежи (лимиты, антифрод и т.п.)
BASE_PROVIDER_FAIL_RATE = 0.03


@app.post("/charge")
def charge(payload: dict):
    time.sleep(random.uniform(0.05, 0.25)) 
    success = random.random() > BASE_PROVIDER_FAIL_RATE
    return {"status": "success" if success else "declined"}


@app.get("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9101
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
