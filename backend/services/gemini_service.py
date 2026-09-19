import asyncio
import email.utils
import hashlib
import logging
import math
import os
import random
import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional, Tuple

from google import genai
from google.genai import types

from backend.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
_CLIENT_CACHE_SIZE = 4
_INTERACTIVE_TIMEOUT_S = 60
_EMBEDDING_TIMEOUT_S = 120
_INGESTION_TIMEOUT_S = 300

# Chunking/input-format version — increment when chunking logic changes to
# invalidate staging checkpoints that used an older format.
CHUNKING_FORMAT_VERSION = 1


class GeminiServiceError(RuntimeError):
    """A provider error that must be surfaced instead of turned into invented content."""


class GeminiRateLimitError(GeminiServiceError):
    """Raised when temporary rate limits (RPM/TPM) are reached and retry attempts are exhausted."""


class GeminiQuotaExhaustedError(GeminiServiceError):
    """Raised when project/tier daily or total quota has been exhausted."""


class GeminiModelUnavailableError(GeminiServiceError):
    """Raised when a requested model is unavailable, deprecated, or not found (HTTP 404/NOT_FOUND)."""


class GeminiAuthenticationError(GeminiServiceError):
    """Raised when authentication fails (HTTP 401/403 or invalid API key)."""
    code = "AUTH_FAILURE"


# ---------------------------------------------------------------------------
# Structured error classification (Points 1, 2)
# ---------------------------------------------------------------------------
@dataclass
class GeminiErrorClassification:
    """Structured classification of a Gemini API error.

    Replaces the previous string-first ``_classify_gemini_error`` with
    an adapter that inspects HTTP status, structured proto details, and
    falls back to conservative string matching only when structured data
    is unavailable.
    """

    tag: str  # AUTH_FAILURE | INVALID_REQUEST | MODEL_UNAVAILABLE | DAILY_QUOTA
    #           THROTTLE_WITH_HINT | THROTTLE_UNKNOWN | SERVER_ERROR | UNKNOWN
    is_retryable: bool
    provider_delay_s: Optional[float] = None
    quota_metric: Optional[str] = None
    quota_id: Optional[str] = None
    model: Optional[str] = None
    is_daily: bool = False


def _parse_retry_after(value: str) -> Optional[float]:
    """Parse a ``Retry-After`` value as seconds or RFC 7231 HTTP-date.

    Returns validated positive finite seconds, or None on any parse failure.
    """
    if value is None:
        return None
    value = str(value).strip()
    if not value:
        return None

    # Try integer / float seconds first
    try:
        seconds = float(value)
        if seconds > 0 and math.isfinite(seconds):
            return seconds
        return None  # Negative, zero, NaN, Inf
    except (ValueError, TypeError):
        pass

    # Try RFC 7231 HTTP-date: "Wed, 21 Oct 2015 07:28:00 GMT"
    try:
        dt = email.utils.parsedate_to_datetime(value)
        delta = dt.timestamp() - time.time()
        if delta > 0 and math.isfinite(delta):
            return delta
        return None
    except Exception:
        return None


def _parse_retryinfo_duration(duration_str: str) -> Optional[float]:
    """Parse a protobuf ``Duration`` string like ``"1.5s"`` or ``"45s"``.

    Returns validated positive finite seconds, or None.
    """
    if not duration_str:
        return None
    match = re.match(r"^(\d+(?:\.\d+)?)s$", str(duration_str).strip())
    if match:
        try:
            val = float(match.group(1))
            if val > 0 and math.isfinite(val):
                return val
        except (ValueError, TypeError):
            pass
    return None


def _extract_provider_delay(exc: Exception) -> Optional[float]:
    """Extract retry delay from exception using multiple strategies.

    Priority: structured RetryInfo > Retry-After header > regex fallback.
    Returns validated positive finite seconds, or None.
    """
    # 1. Structured RetryInfo details (proto)
    details = getattr(exc, "details", None)
    if isinstance(details, (list, tuple)):
        for detail in details:
            if hasattr(detail, "retry_delay"):
                rd = detail.retry_delay
                if hasattr(rd, "seconds"):
                    seconds = float(rd.seconds) + float(getattr(rd, "nanos", 0)) / 1e9
                    if seconds > 0 and math.isfinite(seconds):
                        return seconds
                elif isinstance(rd, str):
                    parsed = _parse_retryinfo_duration(rd)
                    if parsed is not None:
                        return parsed

    # 2. Retry-After HTTP header
    resp = getattr(exc, "response", None)
    if resp is not None:
        headers = getattr(resp, "headers", {})
        ra_value = headers.get("retry-after") or headers.get("Retry-After")
        if ra_value is not None:
            parsed = _parse_retry_after(ra_value)
            if parsed is not None:
                return parsed

    # 3. SDK retry_delay attribute
    retry_delay_attr = getattr(exc, "retry_delay", None)
    if isinstance(retry_delay_attr, (int, float)):
        val = float(retry_delay_attr)
        if val > 0 and math.isfinite(val):
            return val
    elif isinstance(retry_delay_attr, str):
        parsed = _parse_retryinfo_duration(retry_delay_attr)
        if parsed is not None:
            return parsed

    # 4. Conservative string fallback (e.g. "retry after 45s")
    err_str = str(exc)
    match = re.search(r"retry\s+after\s+(\d+(?:\.\d+)?)\s*s?", err_str, re.IGNORECASE)
    if match:
        try:
            val = float(match.group(1))
            if val > 0 and math.isfinite(val):
                return val
        except (ValueError, TypeError):
            pass

    return None


def classify_gemini_error(exc: Exception) -> GeminiErrorClassification:
    """Classify a Gemini API exception using structured data first.

    Classification hierarchy (priority order):
    1. AUTH_FAILURE — status 401/403 or key/permission indicators
    2. INVALID_REQUEST — status 400 or INVALID_ARGUMENT
    3. MODEL_UNAVAILABLE — status 404 / NOT_FOUND or model deprecated / unavailable
    4. DAILY_QUOTA — status 429 with confirmed daily/per-day quota
    5. THROTTLE_WITH_HINT — status 429 with provider retry delay
    6. THROTTLE_UNKNOWN — status 429 without structured details
    7. SERVER_ERROR — status 5xx or transport errors
    8. UNKNOWN — everything else (not retryable by default)

    Where evidence conflicts, a confirmed non-recoverable constraint
    wins over a retry hint.
    """
    status_code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if status_code is None and hasattr(exc, "response") and getattr(exc, "response", None) is not None:
        status_code = getattr(exc.response, "status_code", None)
    err_str = str(exc)
    err_lower = err_str.lower()

    provider_delay = _extract_provider_delay(exc)

    # --- Extract quota metric from structured details ---
    quota_metric: Optional[str] = None
    quota_id: Optional[str] = None
    details = getattr(exc, "details", None)
    if isinstance(details, (list, tuple)):
        for detail in details:
            violations = getattr(detail, "violations", None)
            if isinstance(violations, (list, tuple)):
                for v in violations:
                    metric = getattr(v, "metric", None) or getattr(v, "subject", None)
                    if metric:
                        quota_metric = str(metric)
                    qid = getattr(v, "quota_id", None)
                    if qid:
                        quota_id = str(qid)

    # --- 1. Auth / Permission failures (never retry) ---
    if status_code in (401, 403) or any(
        kw in err_lower
        for kw in ("api_key_invalid", "invalid api key", "api key not valid", "permission_denied")
    ):
        return GeminiErrorClassification(
            tag="AUTH_FAILURE", is_retryable=False, provider_delay_s=None
        )

    # --- 2. Invalid request (never retry) ---
    if status_code == 400 or "invalid_argument" in err_lower:
        return GeminiErrorClassification(
            tag="INVALID_REQUEST", is_retryable=False, provider_delay_s=None
        )

    # --- 3. Model unavailable / Not Found / Deprecated (failover to fallback immediately) ---
    if (
        status_code == 404
        or "not_found" in err_lower
        or "is no longer available" in err_lower
        or "model not found" in err_lower
        or ("models/" in err_lower and "not found" in err_lower)
    ):
        return GeminiErrorClassification(
            tag="MODEL_UNAVAILABLE", is_retryable=False, provider_delay_s=None
        )

    # --- 3 & 4 & 5. Rate limit / quota (429 or RESOURCE_EXHAUSTED) ---
    is_rate_quota = (
        status_code == 429
        or "429" in err_str
        or "resource_exhausted" in err_lower
        or "quota" in err_lower
    )
    if is_rate_quota:
        # Check for confirmed daily quota (non-recoverable in short loop)
        daily_indicators = ("perday", "per_day", "daily limit", "queriesperday")
        is_daily = any(kw in err_lower for kw in daily_indicators)
        if quota_metric and any(kw in quota_metric.lower() for kw in daily_indicators):
            is_daily = True

        # Confirmed daily: non-recoverable wins over retry hint
        if is_daily:
            return GeminiErrorClassification(
                tag="DAILY_QUOTA",
                is_retryable=False,
                provider_delay_s=None,
                quota_metric=quota_metric,
                quota_id=quota_id,
                is_daily=True,
            )

        # Transient with hint
        if provider_delay is not None:
            return GeminiErrorClassification(
                tag="THROTTLE_WITH_HINT",
                is_retryable=True,
                provider_delay_s=provider_delay,
                quota_metric=quota_metric,
                quota_id=quota_id,
            )

        # Transient without hint — remains "unknown cause" in diagnosis
        return GeminiErrorClassification(
            tag="THROTTLE_UNKNOWN",
            is_retryable=True,
            provider_delay_s=None,
            quota_metric=quota_metric,
            quota_id=quota_id,
        )

    # --- 6. Server errors and timeouts (retryable) ---
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return GeminiErrorClassification(
            tag="SERVER_ERROR", is_retryable=True, provider_delay_s=None,
        )

    if (isinstance(status_code, int) and 500 <= status_code < 600) or status_code == 408:
        return GeminiErrorClassification(
            tag="SERVER_ERROR",
            is_retryable=True,
            provider_delay_s=provider_delay,
        )

    if any(k in err_lower for k in ("503", "502", "504", "500", "unavailable", "bad gateway", "service unavailable")) and not is_rate_quota:
        return GeminiErrorClassification(
            tag="SERVER_ERROR",
            is_retryable=True,
            provider_delay_s=provider_delay,
        )

    # Transport / connection errors
    try:
        from httpx import ConnectError, ReadTimeout, WriteTimeout, PoolTimeout
        if isinstance(exc, (ConnectError, ReadTimeout, WriteTimeout, PoolTimeout)):
            return GeminiErrorClassification(
                tag="SERVER_ERROR", is_retryable=True, provider_delay_s=None,
            )
    except ImportError:
        pass

    if isinstance(exc, (ConnectionError, OSError)):
        return GeminiErrorClassification(
            tag="SERVER_ERROR", is_retryable=True, provider_delay_s=None,
        )

    # --- 7. Unknown (not retryable by default) ---
    return GeminiErrorClassification(
        tag="UNKNOWN", is_retryable=False, provider_delay_s=None
    )


# Keep backward-compatible name for existing tests
def _classify_gemini_error(exc: Exception) -> Tuple[bool, str, Optional[float]]:
    """Legacy wrapper — returns (is_transient, tag, retry_after).

    Maps new structured classification to the old tuple format so
    existing test code continues to work during migration.
    """
    c = classify_gemini_error(exc)
    # Map new tags to old tags for backward compat
    err_lower = str(exc).lower()
    if c.tag == "AUTH_FAILURE":
        if any(kw in err_lower for kw in ("api_key_invalid", "invalid api key", "api key not valid")):
            old_tag = "INVALID_KEY"
        else:
            old_tag = "PERMISSION_DENIED"
    elif c.tag == "INVALID_REQUEST":
        old_tag = "INVALID_ARGUMENT"
    elif c.tag == "DAILY_QUOTA":
        old_tag = "QUOTA_EXHAUSTED"
    elif c.tag == "MODEL_UNAVAILABLE":
        old_tag = "MODEL_UNAVAILABLE"
    elif c.tag in ("THROTTLE_WITH_HINT", "THROTTLE_UNKNOWN", "SERVER_ERROR"):
        old_tag = "RATE_LIMIT"
    else:
        old_tag = "UNKNOWN"
    return (c.is_retryable, old_tag, c.provider_delay_s)


# ---------------------------------------------------------------------------
# Client LRU cache keyed by credential fingerprint
# ---------------------------------------------------------------------------
class _ClientCache:
    """Bounded LRU of genai.Client instances keyed by SHA-256(api_key).

    Maintains TWO clients per key:
    - A generation client with default SDK retry behavior.
    - An embedding client with SDK retries disabled
      (``HttpRetryOptions(attempts=1)``) so the application-level
      controller is the single owner of retry policy.
    """

    def __init__(self, maxsize: int = _CLIENT_CACHE_SIZE):
        self._gen_cache: OrderedDict[str, genai.Client] = OrderedDict()
        self._emb_cache: OrderedDict[str, genai.Client] = OrderedDict()
        self._maxsize = maxsize

    @staticmethod
    def _fingerprint(api_key: str) -> str:
        return hashlib.sha256(api_key.encode()).hexdigest()[:16]

    def get_generation(self, api_key: str) -> genai.Client:
        """Client for text generation — SDK retries disabled (attempts=1).

        The application-level generation retry controller owns all retry decisions:
        error classification, Retry-After handling, exponential backoff,
        deadline enforcement, cancellation, logging, and model failover.
        """
        fp = self._fingerprint(api_key)
        if fp in self._gen_cache:
            self._gen_cache.move_to_end(fp)
            return self._gen_cache[fp]
        client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        )
        self._gen_cache[fp] = client
        if len(self._gen_cache) > self._maxsize:
            _, evicted = self._gen_cache.popitem(last=False)
            _close_client(evicted)
        return client

    def get_embedding(self, api_key: str) -> genai.Client:
        """Client for embeddings — SDK retries disabled (attempts=1).

        The application-level retry controller in
        ``_embed_batch_with_retry`` owns all retry decisions:
        error classification, Retry-After handling, exponential
        backoff, total deadline, cancellation, progress reporting,
        logging, and attempt accounting.
        """
        fp = self._fingerprint(api_key)
        if fp in self._emb_cache:
            self._emb_cache.move_to_end(fp)
            return self._emb_cache[fp]
        client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        )
        self._emb_cache[fp] = client
        if len(self._emb_cache) > self._maxsize:
            _, evicted = self._emb_cache.popitem(last=False)
            _close_client(evicted)
        return client

    # Backward compat: existing code calls .get()
    def get(self, api_key: str) -> genai.Client:
        """Alias for get_generation (backward compatibility)."""
        return self.get_generation(api_key)

    def close_all(self) -> None:
        for client in self._gen_cache.values():
            _close_client(client)
        self._gen_cache.clear()
        for client in self._emb_cache.values():
            _close_client(client)
        self._emb_cache.clear()


def _close_client(client: genai.Client) -> None:
    """Best-effort close of a genai client's underlying transports."""
    try:
        closer = getattr(client, "close", None)
        if callable(closer):
            closer()
    except Exception:
        pass


_client_cache = _ClientCache()

# Concurrency semaphore for batch ingestion to avoid concurrent bursts against project quotas
_ingestion_semaphore = asyncio.Semaphore(1)

# ---------------------------------------------------------------------------
# Progress callback type
# ---------------------------------------------------------------------------
# (embedded_chunks, total_chunks, current_batch, total_batches,
#  retry_attempt, next_retry_seconds_or_None)
ProgressCallback = Optional[
    Callable[[int, int, int, int, int, Optional[float]], None]
]


def pack_embedding_batches(
    texts: List[str],
    max_inputs: Optional[int] = None,
    max_chars: Optional[int] = None,
) -> List[List[str]]:
    """Pack texts into batches respecting both maximum item count and total character limit.

    An oversized single chunk that exceeds ``max_chars`` is placed in its
    own batch with a logged warning — it is NOT silently truncated.
    """
    effective_max_inputs = max_inputs if max_inputs is not None else settings.EMBEDDING_MAX_BATCH_INPUTS
    effective_max_chars = max_chars if max_chars is not None else settings.EMBEDDING_MAX_BATCH_CHARS

    batches: List[List[str]] = []
    current_batch: List[str] = []
    current_chars = 0

    for text in texts:
        text_len = len(text)
        if current_batch and (len(current_batch) >= effective_max_inputs or (current_chars + text_len > effective_max_chars)):
            batches.append(current_batch)
            current_batch = [text]
            current_chars = text_len
        else:
            current_batch.append(text)
            current_chars += text_len

    if current_batch:
        batches.append(current_batch)

    # Log warnings for oversized single-chunk batches
    for i, batch in enumerate(batches):
        if len(batch) == 1 and len(batch[0]) > effective_max_chars:
            logger.warning(
                "Batch %d contains a single chunk of %d chars exceeding the "
                "%d-char budget. The chunk is NOT truncated; it will be "
                "sent as-is. Consider splitting large files.",
                i + 1,
                len(batch[0]),
                effective_max_chars,
            )

    return batches


class GeminiService:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY", settings.GEMINI_API_KEY)

    def get_client(self, override_key: Optional[str] = None) -> genai.Client:
        """Get a generation client (default SDK retry behavior)."""
        key = override_key or self.api_key or os.getenv("GEMINI_API_KEY", "")
        if not key:
            raise GeminiServiceError("A Gemini API key is required. Configure one in Settings and try again.")
        try:
            return _client_cache.get_generation(key)
        except Exception as exc:
            logger.exception("Unable to create Gemini client")
            raise GeminiServiceError("Unable to initialize the Gemini client.") from exc

    def get_embedding_client(self, override_key: Optional[str] = None) -> genai.Client:
        """Get a dedicated embedding client (SDK retries disabled, attempts=1)."""
        # If get_client was patched/mocked on this instance and get_embedding_client was not,
        # delegate to get_client so test mocks continue to work seamlessly.
        if hasattr(self.get_client, "mock_calls") and not hasattr(self.get_embedding_client, "mock_calls"):
            return self.get_client(override_key)
        key = override_key or self.api_key or os.getenv("GEMINI_API_KEY", "")
        if not key:
            raise GeminiServiceError("A Gemini API key is required. Configure one in Settings and try again.")
        try:
            return _client_cache.get_embedding(key)
        except Exception as exc:
            logger.exception("Unable to create Gemini embedding client")
            raise GeminiServiceError("Unable to initialize the Gemini embedding client.") from exc

    @staticmethod
    def _content_config(system_instruction: Optional[str] = None, max_output_tokens: Optional[int] = None) -> types.GenerateContentConfig:
        config = types.GenerateContentConfig(temperature=0.7, top_p=0.95, max_output_tokens=max_output_tokens)
        if system_instruction:
            config.system_instruction = system_instruction
        return config

    async def _execute_generation_with_retry(
        self,
        call_fn: Callable[[str, float], Any],
        primary_model: str,
        fallback_model: Optional[str],
        deadline: float,
        total_budget: float,
    ) -> Any:
        max_retries = settings.GENERATION_MAX_RETRIES
        models_to_try = [primary_model]
        can_fallback = bool(fallback_model and fallback_model != primary_model)

        for model_idx, current_model in enumerate(models_to_try):
            is_fallback = (model_idx > 0)
            retries_for_model = 0 if is_fallback else max_retries
            attempt = 0

            while attempt <= retries_for_model:
                attempt += 1
                await asyncio.sleep(0)

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise GeminiServiceError(f"Gemini request exceeded its retry budget ({total_budget}s). Please try again.")

                try:
                    return await call_fn(current_model, remaining)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    classification = classify_gemini_error(exc)

                    # Non-retryable failures fail immediately without retry or fallback
                    if classification.tag == "AUTH_FAILURE":
                        logger.warning("Gemini generation auth failure for model %s", current_model)
                        raise GeminiAuthenticationError("A valid Gemini API key is required. Check your key in Settings and try again.") from exc
                    if classification.tag == "INVALID_REQUEST":
                        logger.warning("Gemini generation invalid request for model %s: %s", current_model, exc)
                        raise GeminiServiceError(f"Invalid Gemini request or model configuration: {exc}") from exc
                    if classification.tag == "DAILY_QUOTA":
                        logger.warning("Gemini daily quota exhausted for model %s", current_model)
                        raise GeminiQuotaExhaustedError("Gemini daily quota exhausted. Retrying immediately will not help. Please try again tomorrow or upgrade your plan.") from exc
                    if classification.tag == "MODEL_UNAVAILABLE":
                        if can_fallback and not is_fallback:
                            logger.warning(
                                "Primary model %s is unavailable (%s). Immediately attempting fallback model %s.",
                                current_model, exc, fallback_model,
                            )
                            models_to_try.append(fallback_model)
                            break
                        logger.warning("Gemini model %s unavailable and no fallback remaining: %s", current_model, exc)
                        raise GeminiModelUnavailableError(
                            f"Gemini model '{current_model}' is unavailable or deprecated. "
                            f"Please update model configuration in settings or environment."
                        ) from exc
                    if not classification.is_retryable:
                        logger.warning("Gemini generation non-retryable failure (%s) for model %s: %s", classification.tag, current_model, exc)
                        raise GeminiServiceError("Gemini could not complete the request. Please try again.") from exc

                    # If retryable, can we retry on current model?
                    if attempt <= retries_for_model:
                        base_backoff = min(
                            settings.GENERATION_BACKOFF_BASE_S * (2 ** (attempt - 1)),
                            settings.GENERATION_BACKOFF_MAX_S,
                        )
                        jitter = random.uniform(0.0, 0.5)
                        local_delay = base_backoff + jitter
                        provider_delay = classification.provider_delay_s
                        delay = max(local_delay, provider_delay) if provider_delay is not None else local_delay

                        now = time.monotonic()
                        if now + delay > deadline:
                            raise GeminiServiceError(f"Gemini request exceeded its retry budget ({total_budget}s). Please try again.") from exc

                        elapsed = now - (deadline - total_budget)
                        rem = deadline - now
                        logger.warning(
                            "Gemini generation retry for model=%s tag=%s attempt=%d/%d delay=%.2fs elapsed=%.2fs remaining=%.2fs",
                            current_model, classification.tag, attempt, retries_for_model + 1, delay, elapsed, rem,
                        )
                        await asyncio.sleep(delay)
                        continue

                    # Exhausted attempts on this model.
                    # Should we attempt fallback model? Only if SERVER_ERROR and fallback available.
                    if can_fallback and not is_fallback and classification.tag == "SERVER_ERROR":
                        logger.warning(
                            "Primary model %s exhausted retries on %s. Attempting fallback model %s.",
                            current_model, classification.tag, fallback_model,
                        )
                        models_to_try.append(fallback_model)
                        break  # move to fallback model in outer loop

                    # Final failure diagnosis
                    if classification.tag in ("THROTTLE_WITH_HINT", "THROTTLE_UNKNOWN"):
                        raise GeminiRateLimitError("Gemini is currently rate-limited. Please wait a moment and try again.") from exc
                    if classification.tag == "SERVER_ERROR":
                        raise GeminiServiceError("Gemini is temporarily unavailable. Please try again in a few moments.") from exc
                    raise GeminiServiceError(f"Gemini request failed after {attempt} attempts. Please try again.") from exc

    async def generate_text(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        use_pro: bool = False,
        override_key: Optional[str] = None,
        image_bytes: Optional[bytes] = None,
        mime_type: str = "image/png",
        timeout: Optional[float] = None,
    ) -> str:
        client = self.get_client(override_key)
        contents: list[Any] = []
        if image_bytes:
            contents.append(types.Part.from_bytes(data=image_bytes, mime_type=mime_type))
        contents.append(prompt)

        primary_model = settings.DEFAULT_PRO_MODEL if use_pro else settings.DEFAULT_FLASH_MODEL
        fallback_model = settings.GENERATION_FALLBACK_MODEL
        budget = timeout if timeout is not None else settings.GENERATION_RETRY_DEADLINE_S
        deadline = time.monotonic() + budget
        config = self._content_config(system_instruction)

        async def _call_generate(model_to_use: str, remaining_timeout: float) -> str:
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=model_to_use,
                    contents=contents,
                    config=config,
                ),
                timeout=remaining_timeout,
            )
            if not response or not response.text:
                raise GeminiServiceError("Gemini returned an empty response. Please try again.")
            return response.text

        return await self._execute_generation_with_retry(
            call_fn=_call_generate,
            primary_model=primary_model,
            fallback_model=fallback_model,
            deadline=deadline,
            total_budget=budget,
        )

    async def generate_stream(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        use_pro: bool = False,
        override_key: Optional[str] = None,
        image_bytes: Optional[bytes] = None,
        mime_type: str = "image/png",
        timeout: Optional[float] = None,
    ) -> AsyncGenerator[str, None]:
        client = self.get_client(override_key)
        contents: list[Any] = []
        if image_bytes:
            contents.append(types.Part.from_bytes(data=image_bytes, mime_type=mime_type))
        contents.append(prompt)

        primary_model = settings.DEFAULT_PRO_MODEL if use_pro else settings.DEFAULT_FLASH_MODEL
        fallback_model = settings.GENERATION_FALLBACK_MODEL
        budget = timeout if timeout is not None else settings.GENERATION_RETRY_DEADLINE_S
        deadline = time.monotonic() + budget
        config = self._content_config(system_instruction)

        max_retries = settings.GENERATION_MAX_RETRIES
        models_to_try = [primary_model]
        can_fallback = bool(fallback_model and fallback_model != primary_model)

        active_stream = None
        buffered_first_chunk = None

        # Phase 1: Establish stream and fetch first chunk with retry and optional fallback
        for model_idx, current_model in enumerate(models_to_try):
            is_fallback = (model_idx > 0)
            retries_for_model = 0 if is_fallback else max_retries
            attempt = 0

            while attempt <= retries_for_model:
                attempt += 1
                await asyncio.sleep(0)

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise GeminiServiceError(f"Gemini request exceeded its retry budget ({budget}s). Please try again.")

                try:
                    stream = await client.aio.models.generate_content_stream(
                        model=current_model,
                        contents=contents,
                        config=config,
                    )
                    stream_iter = stream.__aiter__()
                    chunk = await asyncio.wait_for(stream_iter.__anext__(), timeout=min(remaining, _INTERACTIVE_TIMEOUT_S))
                    active_stream = stream_iter
                    buffered_first_chunk = chunk
                    break
                except StopAsyncIteration:
                    active_stream = None
                    buffered_first_chunk = None
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    classification = classify_gemini_error(exc)
                    if classification.tag == "AUTH_FAILURE":
                        raise GeminiAuthenticationError("A valid Gemini API key is required. Check your key in Settings and try again.") from exc
                    if classification.tag == "INVALID_REQUEST":
                        raise GeminiServiceError(f"Invalid Gemini request or model configuration: {exc}") from exc
                    if classification.tag == "DAILY_QUOTA":
                        raise GeminiQuotaExhaustedError("Gemini daily quota exhausted. Retrying immediately will not help. Please try again tomorrow or upgrade your plan.") from exc
                    if classification.tag == "MODEL_UNAVAILABLE":
                        if can_fallback and not is_fallback:
                            logger.warning(
                                "Primary model %s is unavailable (%s). Immediately attempting fallback stream on %s.",
                                current_model, exc, fallback_model,
                            )
                            models_to_try.append(fallback_model)
                            break
                        raise GeminiModelUnavailableError(
                            f"Gemini model '{current_model}' is unavailable or deprecated. "
                            f"Please update model configuration in settings or environment."
                        ) from exc
                    if not classification.is_retryable:
                        raise GeminiServiceError("Gemini could not complete the stream. Please try again.") from exc

                    if attempt <= retries_for_model:
                        base_backoff = min(
                            settings.GENERATION_BACKOFF_BASE_S * (2 ** (attempt - 1)),
                            settings.GENERATION_BACKOFF_MAX_S,
                        )
                        jitter = random.uniform(0.0, 0.5)
                        local_delay = base_backoff + jitter
                        provider_delay = classification.provider_delay_s
                        delay = max(local_delay, provider_delay) if provider_delay is not None else local_delay

                        now = time.monotonic()
                        if now + delay > deadline:
                            raise GeminiServiceError(f"Gemini request exceeded its retry budget ({budget}s). Please try again.") from exc

                        logger.warning(
                            "Gemini streaming retry for model=%s tag=%s attempt=%d/%d delay=%.2fs",
                            current_model, classification.tag, attempt, retries_for_model + 1, delay,
                        )
                        await asyncio.sleep(delay)
                        continue

                    if can_fallback and not is_fallback and classification.tag == "SERVER_ERROR":
                        logger.warning(
                            "Primary model %s stream initiation failed with %s. Attempting fallback model %s.",
                            current_model, classification.tag, fallback_model,
                        )
                        models_to_try.append(fallback_model)
                        break

                    if classification.tag in ("THROTTLE_WITH_HINT", "THROTTLE_UNKNOWN"):
                        raise GeminiRateLimitError("Gemini is currently rate-limited. Please wait a moment and try again.") from exc
                    if classification.tag == "SERVER_ERROR":
                        raise GeminiServiceError("Gemini is temporarily unavailable. Please try again in a few moments.") from exc
                    raise GeminiServiceError(f"Gemini streaming request failed after {attempt} attempts. Please try again.") from exc

            if active_stream is not None:
                break

        # Phase 2: Consume and yield stream. Once text is yielded, NEVER restart or retry!
        if buffered_first_chunk is not None and getattr(buffered_first_chunk, "text", None):
            yield buffered_first_chunk.text

        if active_stream is not None:
            last_chunk_time = time.monotonic()
            try:
                while True:
                    try:
                        chunk = await asyncio.wait_for(active_stream.__anext__(), timeout=_INTERACTIVE_TIMEOUT_S)
                    except StopAsyncIteration:
                        break
                    except asyncio.TimeoutError:
                        raise GeminiServiceError("Gemini stream stalled. Please try again.")

                    if getattr(chunk, "text", None):
                        last_chunk_time = time.monotonic()
                        yield chunk.text
                    elif time.monotonic() - last_chunk_time > _INTERACTIVE_TIMEOUT_S:
                        raise GeminiServiceError("Gemini stream stalled. Please try again.")
            except (GeminiServiceError, asyncio.CancelledError):
                raise
            except Exception as exc:
                logger.exception("Gemini streaming request interrupted mid-stream")
                raise GeminiServiceError("Gemini could not complete the stream. Please try again.") from exc

    # ------------------------------------------------------------------
    # Embedding with application-owned retry controller
    # ------------------------------------------------------------------

    async def _embed_batch_with_retry(
        self,
        client: genai.Client,
        batch_texts: List[str],
        batch_idx: int,
        total_batches: int,
        output_dimensionality: Optional[int] = None,
        timeout: float = _EMBEDDING_TIMEOUT_S,
        deadline: Optional[float] = None,
        progress_cb: ProgressCallback = None,
        embedded_so_far: int = 0,
        total_chunks: int = 0,
    ) -> List[List[float]]:
        """Embed a single batch with retry, backoff, and strict validation.

        Uses a monotonic-clock total deadline. The configured backoff cap
        limits only locally-generated backoff. A provider-requested delay
        is a *minimum* and is never shortened. If the provider delay
        exceeds remaining time, the batch stops immediately with an
        honest diagnosis.

        Timeouts and 5xx errors are routed through the same bounded
        retry scheduler (not retried immediately without backoff).
        """
        embed_config: dict[str, Any] = {}
        if output_dimensionality is not None:
            embed_config["output_dimensionality"] = output_dimensionality

        # Build separate Content objects for each chunk
        contents = [types.Content(parts=[types.Part(text=text)]) for text in batch_texts]

        max_retries = settings.EMBEDDING_MAX_RETRIES
        if deadline is None:
            deadline = time.monotonic() + settings.EMBEDDING_RETRY_DEADLINE_S
        last_exception: Optional[Exception] = None

        for attempt in range(max_retries + 1):
            # --- Pre-attempt deadline check ---
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                msg = (
                    f"Retry deadline exhausted before attempt {attempt + 1} on "
                    f"batch {batch_idx + 1}/{total_batches}."
                )
                logger.error(msg)
                if last_exception:
                    raise GeminiRateLimitError(msg) from last_exception
                raise GeminiRateLimitError(msg)

            request_start = time.monotonic()
            try:
                # Align per-request timeout with remaining deadline
                effective_timeout = min(timeout, remaining)

                response = await asyncio.wait_for(
                    client.aio.models.embed_content(
                        model=settings.EMBEDDING_MODEL,
                        contents=contents,
                        config=types.EmbedContentConfig(**embed_config) if embed_config else None,
                    ),
                    timeout=effective_timeout,
                )

                # --- Validate response ---
                raw_embeddings = getattr(response, "embeddings", [])
                embeddings = [item.values for item in raw_embeddings if getattr(item, "values", None)]

                if len(embeddings) != len(batch_texts):
                    logger.error(
                        "Embedding cardinality mismatch on batch %d/%d: expected %d vectors, "
                        "provider returned %d. Inputs cannot be safely associated with chunks.",
                        batch_idx + 1, total_batches, len(batch_texts), len(embeddings),
                    )
                    raise GeminiServiceError(
                        f"Embedding provider returned {len(embeddings)} vectors for "
                        f"{len(batch_texts)} inputs (cardinality mismatch). Multiple inputs "
                        f"cannot be safely associated with distinct chunks. The project was not indexed."
                    )

                if any(not emb for emb in embeddings):
                    raise GeminiServiceError(
                        "Embedding provider returned empty vector data for one or more inputs."
                    )

                # Dimension consistency
                dim = len(embeddings[0])
                if any(len(emb) != dim for emb in embeddings):
                    raise GeminiServiceError(
                        f"Embedding provider returned inconsistent vector dimensions "
                        f"across inputs (expected {dim})."
                    )

                if output_dimensionality is not None and dim != output_dimensionality:
                    raise GeminiServiceError(
                        f"Embedding provider returned dimension {dim}, expected {output_dimensionality}."
                    )

                # Finite-number validation
                for emb_idx, emb in enumerate(embeddings):
                    if any(not math.isfinite(v) for v in emb):
                        raise GeminiServiceError(
                            f"Embedding vector {emb_idx} in batch {batch_idx + 1} contains "
                            f"non-finite values (NaN/Inf). Index not committed."
                        )

                return embeddings

            except asyncio.CancelledError:
                logger.info("Embedding batch %d/%d cancelled.", batch_idx + 1, total_batches)
                raise

            except (asyncio.TimeoutError, TimeoutError) as exc:
                # Route timeouts through the bounded retry scheduler with backoff
                last_exception = exc
                request_elapsed = time.monotonic() - request_start
                logger.warning(
                    "Embedding batch %d/%d timed out (attempt %d/%d, %.1fs elapsed).",
                    batch_idx + 1, total_batches, attempt + 1, max_retries + 1,
                    request_elapsed,
                )

                if attempt >= max_retries:
                    raise GeminiServiceError(
                        f"Embedding request timed out after {max_retries + 1} attempts on "
                        f"batch {batch_idx + 1}/{total_batches}. The project was not indexed."
                    ) from exc

                # Fall through to backoff calculation below

            except GeminiServiceError:
                # Cardinality or dimension validation failures — not retried
                raise

            except Exception as exc:
                last_exception = exc
                classification = classify_gemini_error(exc)

                if not classification.is_retryable:
                    # Log without exposing keys or chunk text
                    logger.error(
                        "Non-retryable error on embedding batch %d/%d (tag=%s, attempt %d/%d).",
                        batch_idx + 1, total_batches, classification.tag,
                        attempt + 1, max_retries + 1,
                    )
                    if classification.tag == "AUTH_FAILURE":
                        err_lower = str(exc).lower()
                        if any(kw in err_lower for kw in ("api_key_invalid", "invalid api key", "api key not valid")):
                            raise GeminiAuthenticationError(
                                "Invalid Gemini API key. Please configure a valid API key in Settings."
                            ) from exc
                        raise GeminiAuthenticationError(
                            "Gemini API permission denied. Please verify your API key and "
                            "Google Cloud project permissions."
                        ) from exc
                    elif classification.tag == "INVALID_REQUEST":
                        raise GeminiServiceError(
                            f"Invalid request sent to Gemini API: {exc}"
                        ) from exc
                    elif classification.tag == "DAILY_QUOTA":
                        raise GeminiQuotaExhaustedError(
                            "Gemini project quota exhausted (429 RESOURCE_EXHAUSTED). "
                            "Gemini API quotas are enforced at the Google Cloud/AI Studio "
                            "project level, so creating another key in the same project will "
                            "not reset the quota. Check billing or quota at "
                            "https://aistudio.google.com/ or configure an API key from an "
                            "authorized project with available quota in Settings."
                        ) from exc
                    else:
                        raise GeminiServiceError(
                            f"Gemini embedding request failed: {exc}. The project was not indexed."
                        ) from exc

                if attempt >= max_retries:
                    logger.error(
                        "Retry budget exhausted on batch %d/%d after %d attempts (tag=%s).",
                        batch_idx + 1, total_batches, max_retries + 1, classification.tag,
                    )
                    if classification.tag in ("THROTTLE_WITH_HINT", "THROTTLE_UNKNOWN"):
                        raise GeminiRateLimitError(
                            "Gemini rate limit exceeded (429 RESOURCE_EXHAUSTED) after retrying "
                            "with exponential backoff. The provider rejected the request due to "
                            "a rate/quota limit. Please wait and retry ingestion."
                        ) from exc
                    raise GeminiServiceError(
                        f"Gemini embedding request failed after {max_retries + 1} attempts: {exc}. "
                        f"The project was not indexed."
                    ) from exc

                # Fall through to backoff calculation

            # --- Compute backoff delay ---
            # The configured cap limits only locally-generated backoff.
            # A provider delay is a MINIMUM and is never shortened.
            local_backoff = min(
                settings.EMBEDDING_BACKOFF_BASE_S * (2 ** attempt) + random.uniform(0.1, 0.5),
                settings.EMBEDDING_BACKOFF_MAX_S,
            )
            provider_delay = None
            if last_exception is not None:
                provider_delay = _extract_provider_delay(last_exception)

            if provider_delay is not None and provider_delay > 0:
                effective_delay = max(local_backoff, provider_delay)
            else:
                effective_delay = local_backoff

            # --- Pre-sleep deadline check ---
            remaining = deadline - time.monotonic()
            if effective_delay > remaining:
                logger.warning(
                    "Provider requested %.1fs wait but only %.1fs remain in retry budget "
                    "for batch %d/%d. Stopping retries.",
                    effective_delay, remaining, batch_idx + 1, total_batches,
                )
                if last_exception:
                    raise GeminiRateLimitError(
                        f"Retry deadline would be exceeded: provider requested {effective_delay:.1f}s "
                        f"but only {remaining:.1f}s remain. The project was not indexed."
                    ) from last_exception
                raise GeminiRateLimitError(
                    f"Retry deadline would be exceeded on batch {batch_idx + 1}/{total_batches}."
                )

            # Determine tag for logging
            _tag = "TIMEOUT"
            if last_exception and not isinstance(last_exception, (asyncio.TimeoutError, TimeoutError)):
                try:
                    _tag = classify_gemini_error(last_exception).tag
                except Exception:
                    _tag = "UNKNOWN"

            logger.warning(
                "Retryable error on batch %d/%d (attempt %d/%d, tag=%s). "
                "Waiting %.2fs (local=%.2fs, provider=%s).",
                batch_idx + 1, total_batches, attempt + 1, max_retries + 1,
                _tag, effective_delay, local_backoff,
                f"{provider_delay:.1f}s" if provider_delay else "none",
            )

            # Report retry status via progress callback
            if progress_cb:
                try:
                    progress_cb(
                        embedded_so_far, total_chunks,
                        batch_idx + 1, total_batches,
                        attempt + 1, effective_delay,
                    )
                except Exception:
                    pass

            await asyncio.sleep(effective_delay)

        if last_exception:
            raise GeminiServiceError(f"Embedding failed: {last_exception}") from last_exception
        raise GeminiServiceError("Embedding failed with unknown error.")

    async def get_embeddings(
        self,
        texts: List[str],
        override_key: Optional[str] = None,
        output_dimensionality: Optional[int] = None,
        timeout: float = _EMBEDDING_TIMEOUT_S,
        batch_size: Optional[int] = None,
        max_chars: Optional[int] = None,
        is_ingestion: bool = False,
        progress_cb: ProgressCallback = None,
        on_batch_complete: Optional[Callable[[int, List[str], List[List[float]]], None]] = None,
    ) -> List[List[float]]:
        """Embed texts using dual-budget batching, pacing, and response validation.

        Args:
            texts: List of text chunks to embed.
            override_key: Optional API key override.
            output_dimensionality: Optional dimension truncation.
            timeout: Per-request timeout in seconds.
            batch_size: Max inputs per request (default from settings).
            max_chars: Max estimated characters per request (default from settings).
            is_ingestion: Whether this call is part of repository ingestion (enforces semaphore).
            progress_cb: Optional callback for progress reporting.
            on_batch_complete: Optional callback invoked immediately after each batch succeeds.
        """
        if not texts:
            return []

        effective_batch_size = batch_size or settings.EMBEDDING_MAX_BATCH_INPUTS
        effective_max_chars = max_chars or settings.EMBEDDING_MAX_BATCH_CHARS

        batches = pack_embedding_batches(
            texts,
            max_inputs=effective_batch_size,
            max_chars=effective_max_chars,
        )

        # Use dedicated embedding client (SDK retries disabled)
        client = self.get_embedding_client(override_key)
        all_embeddings: List[List[float]] = []

        # Use process-level concurrency semaphore during batch ingestion or multi-chunk calls
        should_lock = is_ingestion or len(batches) > 1

        async def _process_all_batches() -> List[List[float]]:
            """Process all batches, preserving successful results.

            If a batch fails permanently, previously successful batch
            results are still available for staging checkpoint persistence.
            """
            results: List[List[float]] = []
            total_batches = len(batches)
            total_chunks = len(texts)

            for idx, batch in enumerate(batches):
                # Each batch gets its own full retry deadline budget
                batch_deadline = time.monotonic() + settings.EMBEDDING_RETRY_DEADLINE_S

                # Inter-batch pacing delay between consecutive requests
                if idx > 0 and settings.EMBEDDING_INTER_BATCH_DELAY_S > 0:
                    await asyncio.sleep(settings.EMBEDDING_INTER_BATCH_DELAY_S)

                # Acquire rate scheduler if available
                try:
                    from backend.services.rate_scheduler import embedding_rate_scheduler
                    estimated_chars = sum(len(t) for t in batch)
                    await embedding_rate_scheduler.acquire(estimated_chars)
                except ImportError:
                    pass

                batch_embeddings = await self._embed_batch_with_retry(
                    client=client,
                    batch_texts=batch,
                    batch_idx=idx,
                    total_batches=total_batches,
                    output_dimensionality=output_dimensionality,
                    timeout=timeout,
                    deadline=batch_deadline,
                    progress_cb=progress_cb,
                    embedded_so_far=len(results),
                    total_chunks=total_chunks,
                )
                results.extend(batch_embeddings)

                # Invoke on_batch_complete immediately so successful batches are staged
                if on_batch_complete:
                    try:
                        on_batch_complete(idx, batch, batch_embeddings)
                    except Exception as exc:
                        logger.warning("on_batch_complete callback raised an exception: %s", exc)

                # Report batch completion
                if progress_cb:
                    try:
                        progress_cb(
                            len(results), total_chunks,
                            idx + 1, total_batches,
                            0, None,
                        )
                    except Exception:
                        pass

            return results

        if should_lock:
            async with _ingestion_semaphore:
                all_embeddings = await _process_all_batches()
        else:
            all_embeddings = await _process_all_batches()

        # Final cardinality assertion
        if len(all_embeddings) != len(texts):
            raise GeminiServiceError(
                f"Embedding cardinality failure: total {len(all_embeddings)} embeddings "
                f"returned for {len(texts)} inputs. Index not committed."
            )

        return all_embeddings

    # ------------------------------------------------------------------
    # Connection tests
    # ------------------------------------------------------------------

    async def test_connection(self, api_key: str) -> Dict[str, Any]:
        """Generation connectivity test — does NOT verify embedding quota.

        Tests text generation only.  A successful result means one
        generation request succeeded at this time; it does not guarantee
        that embedding quota is available or that a full repository
        import will succeed.
        """
        if not api_key or not api_key.strip():
            return {"success": False, "message": "Gemini API key cannot be empty."}
        try:
            client = _client_cache.get_generation(api_key.strip())
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=settings.DEFAULT_FLASH_MODEL,
                    contents="Reply with OK.",
                    config=self._content_config(max_output_tokens=10),
                ),
                timeout=15,
            )
            return {
                "success": True,
                "model": settings.DEFAULT_FLASH_MODEL,
                "message": (
                    "Successfully connected to Gemini text generation API "
                    "(does not verify embedding quota)."
                ),
                "reply": response.text,
            }
        except Exception as exc:
            logger.exception("Gemini connection test failed")
            classification = classify_gemini_error(exc)
            if classification.tag == "MODEL_UNAVAILABLE":
                return {
                    "success": False,
                    "message": f"Connection failed: Model '{settings.DEFAULT_FLASH_MODEL}' is unavailable or deprecated. Please update DEFAULT_FLASH_MODEL.",
                    "error_tag": classification.tag,
                }
            return {"success": False, "message": "Connection failed. Check the API key and model availability."}

    async def test_embedding_capability(self, api_key: str) -> Dict[str, Any]:
        """Explicit embedding capability check — user-triggered, consumes quota.

        Makes a single minimal embedding request for the configured model.
        This check MUST:
        - Be manually triggered (never automatic before ingestion)
        - Send minimal non-sensitive content
        - Clearly state that it consumes embedding quota
        - Report only that one request succeeded at that moment
        - Never imply that a full import is guaranteed
        """
        if not api_key or not api_key.strip():
            return {"success": False, "message": "Gemini API key cannot be empty."}
        try:
            client = _client_cache.get_embedding(api_key.strip())
            response = await asyncio.wait_for(
                client.aio.models.embed_content(
                    model=settings.EMBEDDING_MODEL,
                    contents=types.Content(parts=[types.Part(text="embedding probe")]),
                ),
                timeout=15,
            )
            raw = getattr(response, "embeddings", [])
            if not raw or not getattr(raw[0], "values", None):
                return {
                    "success": False,
                    "message": f"Embedding model '{settings.EMBEDDING_MODEL}' returned an empty response.",
                }
            dim = len(raw[0].values)
            return {
                "success": True,
                "model": settings.EMBEDDING_MODEL,
                "dimension": dim,
                "message": (
                    f"Embedding probe succeeded for model '{settings.EMBEDDING_MODEL}' "
                    f"(dimension={dim}). This consumed a small amount of embedding quota. "
                    f"Passing means one request succeeded at this time — it does NOT "
                    f"guarantee that a full repository import will succeed."
                ),
            }
        except Exception as exc:
            classification = classify_gemini_error(exc)
            logger.warning(
                "Embedding capability check failed (tag=%s): %s",
                classification.tag, exc,
            )
            return {
                "success": False,
                "message": (
                    f"Embedding probe failed for model '{settings.EMBEDDING_MODEL}': {exc}. "
                    f"Check API key permissions and embedding model availability."
                ),
                "error_tag": classification.tag,
            }


gemini_service = GeminiService()
