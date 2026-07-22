"""Per-IP token buckets (Phase D, 2026-07-22) — stdlib only, in-memory.

Good enough for a single-process publisher; a multi-replica deploy would
move this to the reverse proxy or a shared store. Buckets refill
continuously; an empty bucket = HTTP 429 from the middleware in app.py.
"""
import threading
import time


class TokenBucket:
    def __init__(self, per_minute: float, burst: float):
        self.rate = per_minute / 60.0
        self.burst = float(burst)
        self._levels: dict[str, tuple[float, float]] = {}  # key -> (tokens, ts)
        self._lock = threading.Lock()

    def allow(self, key: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            tokens, last = self._levels.get(key, (self.burst, now))
            tokens = min(self.burst, tokens + (now - last) * self.rate)
            if tokens < 1.0:
                self._levels[key] = (tokens, now)
                return False
            self._levels[key] = (tokens - 1.0, now)
            if len(self._levels) > 10_000:   # bound memory under abuse
                cutoff = now - 600
                self._levels = {k: v for k, v in self._levels.items()
                                if v[1] >= cutoff}
            return True


class Limiter:
    """Route-class buckets from the publisher.rate_limit config block."""

    def __init__(self, rl_cfg: dict):
        self.enabled = bool(rl_cfg.get("enabled", True))
        self.auth = TokenBucket(rl_cfg.get("auth_per_minute", 3),
                                rl_cfg.get("auth_burst", 5))
        self.billing = TokenBucket(rl_cfg.get("billing_per_minute", 6),
                                   rl_cfg.get("billing_burst", 10))
        self.global_ = TokenBucket(rl_cfg.get("global_per_minute", 60),
                                   rl_cfg.get("global_burst", 120))

    def check(self, path: str, ip: str) -> bool:
        """False = over the limit. Strictest matching bucket wins; the
        global bucket always applies too."""
        if not self.enabled:
            return True
        ok = self.global_.allow(ip)
        if path.startswith("/auth/request-link"):
            ok = self.auth.allow(ip) and ok
        elif path.startswith("/billing/"):
            ok = self.billing.allow(ip) and ok
        return ok
