"""Embedding rate scheduler for RepoTalk.

Process-scoped, monotonic-clock-based scheduler that paces embedding
API requests.  All embedding calls (ingestion batches AND interactive
query embeddings) acquire from the same scheduler.

Behaviour depends on configuration:

* **When RPM/TPM limits are configured**: enforces them with a
  monotonic-clock sliding-window.
* **When limits are unset (default)**: uses configurable minimum
  request spacing (``EMBEDDING_MIN_REQUEST_INTERVAL_S``) and
  provider-cooldown observation.  RepoTalk cannot guarantee quota
  compliance without actual project limits — best-effort pacing only.

Limitations (documented, not hidden):
- Coordination is process-local.  Multi-worker deployments need
  external shared coordination (not implemented).
- Estimated token counts use ``len(text) / 4`` with a 25% safety
  margin.  This is an approximation, not accurate tokenization.
"""

import asyncio
import logging
import time
from typing import Optional

from backend.config import settings

logger = logging.getLogger(__name__)


class EmbeddingRateScheduler:
    """Process-scoped async scheduler for embedding API requests.

    Uses monotonic clock for all timing to avoid wall-clock skew.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        # Monotonic timestamp of last request
        self._last_request_time: float = 0.0
        # Sliding window for RPM tracking (list of monotonic timestamps)
        self._request_timestamps: list[float] = []
        # Sliding window for estimated TPM tracking (list of (timestamp, estimated_tokens))
        self._token_timestamps: list[tuple[float, float]] = []
        # Provider-requested cooldown end time (monotonic)
        self._cooldown_until: float = 0.0
        # Configuration cached at construction
        self._rpm_limit: Optional[int] = settings.EMBEDDING_RPM_LIMIT
        self._tpm_limit: Optional[int] = settings.EMBEDDING_TPM_LIMIT
        self._min_interval: float = settings.EMBEDDING_MIN_REQUEST_INTERVAL_S

        if self._rpm_limit:
            logger.info("Rate scheduler: RPM limit configured at %d", self._rpm_limit)
        if self._tpm_limit:
            logger.info("Rate scheduler: TPM limit configured at %d", self._tpm_limit)
        if not self._rpm_limit and not self._tpm_limit:
            logger.info(
                "Rate scheduler: no RPM/TPM limits configured — using best-effort "
                "pacing (min_interval=%.2fs). RepoTalk cannot guarantee quota "
                "compliance without actual project limits.",
                self._min_interval,
            )

    async def acquire(self, estimated_chars: int = 0) -> None:
        """Wait until the rate budget allows another request.

        Args:
            estimated_chars: Estimated total characters in the request.
                Used for TPM estimation (chars / 4 with 25% safety margin).
        """
        # Conservative token estimate: chars/4 * 1.25 safety margin
        estimated_tokens = (estimated_chars / 4.0) * 1.25 if estimated_chars > 0 else 0

        async with self._lock:
            now = time.monotonic()

            # --- 1. Provider cooldown ---
            if now < self._cooldown_until:
                wait = self._cooldown_until - now
                logger.info("Rate scheduler: waiting %.1fs for provider cooldown.", wait)
                await asyncio.sleep(wait)
                now = time.monotonic()

            # --- 2. Minimum request spacing (always enforced) ---
            if self._min_interval > 0 and self._last_request_time > 0:
                elapsed = now - self._last_request_time
                if elapsed < self._min_interval:
                    wait = self._min_interval - elapsed
                    await asyncio.sleep(wait)
                    now = time.monotonic()

            # --- 3. RPM sliding window ---
            if self._rpm_limit:
                window_start = now - 60.0
                self._request_timestamps = [
                    ts for ts in self._request_timestamps if ts > window_start
                ]
                while len(self._request_timestamps) >= self._rpm_limit:
                    oldest = self._request_timestamps[0]
                    wait = (oldest + 60.0) - now + 0.1  # small buffer
                    if wait > 0:
                        logger.info(
                            "Rate scheduler: RPM limit (%d) reached, waiting %.1fs.",
                            self._rpm_limit, wait,
                        )
                        await asyncio.sleep(wait)
                        now = time.monotonic()
                    window_start = now - 60.0
                    self._request_timestamps = [
                        ts for ts in self._request_timestamps if ts > window_start
                    ]

            # --- 4. Estimated TPM sliding window ---
            if self._tpm_limit and estimated_tokens > 0:
                window_start = now - 60.0
                self._token_timestamps = [
                    (ts, tok) for ts, tok in self._token_timestamps if ts > window_start
                ]
                current_tokens = sum(tok for _, tok in self._token_timestamps)
                while current_tokens + estimated_tokens > self._tpm_limit:
                    if not self._token_timestamps:
                        break
                    oldest_ts, _ = self._token_timestamps[0]
                    wait = (oldest_ts + 60.0) - now + 0.1
                    if wait > 0:
                        logger.info(
                            "Rate scheduler: estimated TPM limit (%d) reached "
                            "(current ~%.0f + ~%.0f), waiting %.1fs.",
                            self._tpm_limit, current_tokens, estimated_tokens, wait,
                        )
                        await asyncio.sleep(wait)
                        now = time.monotonic()
                    window_start = now - 60.0
                    self._token_timestamps = [
                        (ts, tok) for ts, tok in self._token_timestamps if ts > window_start
                    ]
                    current_tokens = sum(tok for _, tok in self._token_timestamps)

            # --- Record this request ---
            self._last_request_time = now
            if self._rpm_limit:
                self._request_timestamps.append(now)
            if self._tpm_limit and estimated_tokens > 0:
                self._token_timestamps.append((now, estimated_tokens))

    def observe_cooldown(self, delay_s: float) -> None:
        """Record a provider-requested cooldown period.

        Called when a 429 with Retry-After is received.  All subsequent
        ``acquire()`` calls will wait until the cooldown expires.
        """
        if delay_s > 0:
            end = time.monotonic() + delay_s
            if end > self._cooldown_until:
                self._cooldown_until = end
                logger.info("Rate scheduler: provider cooldown set for %.1fs.", delay_s)

    def reset(self) -> None:
        """Reset all tracking state (useful for testing)."""
        self._last_request_time = 0.0
        self._request_timestamps.clear()
        self._token_timestamps.clear()
        self._cooldown_until = 0.0


# Process-scoped singleton
embedding_rate_scheduler = EmbeddingRateScheduler()
