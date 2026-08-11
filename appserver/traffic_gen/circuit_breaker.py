"""
Circuit breaker для платёжных провайдеров + роутер, выбирающий, куда
направить очередную транзакцию.

Состояния (классическая схема):
  CLOSED    — провайдер принимает трафик как обычно.
  OPEN      — конверсия ушла ниже порога -> провайдер исключён из
              роутинга на cooldown_sec, весь трафик уходит на других.
  HALF_OPEN — после cooldown пробуем небольшую долю трафика; если
              конверсия восстановилась — CLOSED, если нет — снова OPEN.
"""
import random
import threading
import time
from collections import deque
from enum import Enum


class State(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class ProviderCircuitBreaker:
    def __init__(self, provider_id, window_size=20, fail_threshold=0.4,
                 cooldown_sec=30, half_open_probes=5):
        self.provider_id = provider_id
        self.window = deque(maxlen=window_size)
        self.fail_threshold = fail_threshold
        self.cooldown_sec = cooldown_sec
        self.half_open_probes = half_open_probes
        self.state = State.CLOSED
        self.opened_at = None
        self.half_open_successes = 0
        self.half_open_attempts = 0
        self._lock = threading.Lock()

    def record(self, success: bool):
        """Вызывается после каждого реального обращения к провайдеру"""
        with self._lock:
            self.window.append(success)

            if self.state == State.HALF_OPEN:
                self.half_open_attempts += 1
                if success:
                    self.half_open_successes += 1
                if self.half_open_attempts >= self.half_open_probes:
                    conversion = self.half_open_successes / self.half_open_attempts
                    if conversion >= (1 - self.fail_threshold):
                        self._close()
                    else:
                        self._open()
                return

            if len(self.window) >= self.window.maxlen:
                fail_rate = 1 - (sum(self.window) / len(self.window))
                if fail_rate > self.fail_threshold:
                    self._open()

    def allow_request(self) -> bool:
        """Можно ли пускать трафик на этого провайдера прямо сейчас"""
        with self._lock:
            if self.state == State.CLOSED:
                return True
            if self.state == State.OPEN:
                if self.opened_at is not None and time.time() - self.opened_at >= self.cooldown_sec:
                    self._half_open()
                    return True
                return False
            return True  

    def conversion_rate(self) -> float:
        with self._lock:
            if not self.window:
                return 1.0
            return sum(self.window) / len(self.window)

    def force_open(self):
        """Ручное принудительное размыкание - для сценария из меню деградации"""
        with self._lock:
            self._open()

    def _open(self):
        self.state = State.OPEN
        self.opened_at = time.time()

    def _half_open(self):
        self.state = State.HALF_OPEN
        self.half_open_successes = 0
        self.half_open_attempts = 0

    def _close(self):
        self.state = State.CLOSED
        self.window.clear()


class ProviderRouter:
    """Держит по одному breaker'у на провайдера и выбирает, куда слать трафик."""

    def __init__(self, provider_ids, **cb_kwargs):
        self.breakers = {pid: ProviderCircuitBreaker(pid, **cb_kwargs) for pid in provider_ids}

    def pick_provider(self, preferred=None):
        if preferred is not None and self.breakers[preferred].allow_request():
            return preferred

        available = [pid for pid, cb in self.breakers.items() if cb.allow_request()]
        if available:
            return random.choice(available)

        return max(self.breakers, key=lambda p: self.breakers[p].conversion_rate())

    def report(self, provider_id, success: bool):
        self.breakers[provider_id].record(success)
