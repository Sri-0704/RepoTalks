import asyncio
import hashlib
import json
import logging
import math
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Dict, List, Literal, Optional, Tuple

from backend.config import settings
from backend.services.gemini_service import (
    GeminiAuthenticationError,
    GeminiModelUnavailableError,
    GeminiQuotaExhaustedError,
    GeminiRateLimitError,
    GeminiServiceError,
    gemini_service,
)
from backend.services.groq_service import (
    GroqAuthenticationError,
    GroqCapacityError,
    GroqEmptyResponseError,
    GroqInvalidRequestError,
    GroqModelUnavailableError,
    GroqRateLimitError,
    GroqServiceError,
    GroqTimeoutError,
    groq_service,
)

logger = logging.getLogger("repotalks.llm_gateway")

ProviderName = Literal["groq", "gemini"]
ProviderPreference = Literal["auto", "groq", "gemini"]


# ---------------------------------------------------------------------------
# Provider-Neutral Exceptions
# ---------------------------------------------------------------------------
class LLMServiceError(RuntimeError):
    """Base provider-neutral error for text generation."""

    def __init__(
        self,
        message: str,
        error_code: str = "LLM_SERVICE_ERROR",
        attempted_providers: Optional[List[str]] = None,
        status_code: int = 503,
    ):
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.attempted_providers = attempted_providers or []
        self.status_code = status_code


class LLMEmptyContentError(LLMServiceError):
    """Raised when provider finishes without emitting non-whitespace answer content."""

    def __init__(self, message: str, attempted_providers: Optional[List[str]] = None):
        super().__init__(message, error_code="EMPTY_CONTENT", attempted_providers=attempted_providers, status_code=502)


class LLMAuthenticationError(LLMServiceError):
    """Raised on authentication or permission failure (HTTP 401/403)."""

    def __init__(self, message: str, attempted_providers: Optional[List[str]] = None):
        super().__init__(message, error_code="AUTH_FAILURE", attempted_providers=attempted_providers, status_code=401)


class LLMInvalidRequestError(LLMServiceError):
    """Raised on invalid request parameters or unsupported input (HTTP 400/422)."""

    def __init__(self, message: str, attempted_providers: Optional[List[str]] = None):
        super().__init__(message, error_code="INVALID_REQUEST", attempted_providers=attempted_providers, status_code=400)


class LLMRateLimitError(LLMServiceError):
    """Raised on temporary rate limits (RPM/TPM)."""

    def __init__(self, message: str, attempted_providers: Optional[List[str]] = None, retry_after: Optional[float] = None):
        super().__init__(message, error_code="RATE_LIMIT", attempted_providers=attempted_providers, status_code=429)
        self.retry_after = retry_after


class LLMQuotaExhaustedError(LLMServiceError):
    """Raised on daily or project quota exhaustion."""

    def __init__(self, message: str, attempted_providers: Optional[List[str]] = None):
        super().__init__(message, error_code="QUOTA_EXHAUSTED", attempted_providers=attempted_providers, status_code=503)


class LLMModelUnavailableError(LLMServiceError):
    """Raised when model is not found or deprecated (HTTP 404)."""

    def __init__(self, message: str, attempted_providers: Optional[List[str]] = None):
        super().__init__(message, error_code="MODEL_UNAVAILABLE", attempted_providers=attempted_providers, status_code=503)


class LLMTimeoutError(LLMServiceError):
    """Raised when request exceeds overall deadline."""

    def __init__(self, message: str, attempted_providers: Optional[List[str]] = None):
        super().__init__(message, error_code="TIMEOUT", attempted_providers=attempted_providers, status_code=504)


class LLMCapabilityError(LLMServiceError):
    """Raised when an operation is unsupported by the selected provider."""

    def __init__(self, message: str, attempted_providers: Optional[List[str]] = None):
        super().__init__(message, error_code="CAPABILITY_UNSUPPORTED", attempted_providers=attempted_providers, status_code=400)


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------
@dataclass
class GenerationCredentials:
    gemini_api_key: Optional[str] = None
    groq_api_key: Optional[str] = None
    provider_preference: Optional[ProviderPreference] = None
    provenance: str = "request"


@dataclass
class GenerationRequest:
    prompt: str
    task_name: str = "generation"
    system_instruction: Optional[str] = None
    preference: Optional[ProviderPreference] = None
    use_high_quality: bool = False
    output_schema: Optional[Dict[str, Any]] = None
    json_object_mode: bool = False
    image_bytes: Optional[bytes] = None
    mime_type: str = "image/png"
    timeout: Optional[float] = None
    first_content_timeout: Optional[float] = None
    max_output_tokens: Optional[int] = None


@dataclass
class GenerationResult:
    text: str
    provider: ProviderName
    model: str
    fallback_used: bool = False
    fallback_reason: Optional[str] = None
    elapsed_ms: float = 0.0


# ---------------------------------------------------------------------------
# In-Memory Cooldown Map (Thundering-Herd Protection)
# ---------------------------------------------------------------------------
class CooldownTracker:
    """Tracks temporarily throttled credentials using SHA-256 digests.

    Never stores keys. Caps maximum entries and auto-prunes expired records.
    """

    def __init__(self, max_entries: int = 100):
        self._max_entries = max_entries
        self._cooldowns: Dict[str, float] = {}

    @staticmethod
    def _fingerprint(provider: str, key: str) -> str:
        digest = hashlib.sha256(key.strip().encode("utf-8")).hexdigest()[:16]
        return f"{provider}:{digest}"

    def set_cooldown(self, provider: str, key: str, duration_seconds: float) -> None:
        if not key:
            return
        now = time.monotonic()
        fp = self._fingerprint(provider, key)
        self._prune(now)
        if len(self._cooldowns) >= self._max_entries:
            # Drop oldest entry
            oldest_key = min(self._cooldowns, key=self._cooldowns.get)
            self._cooldowns.pop(oldest_key, None)
        self._cooldowns[fp] = now + max(duration_seconds, 5.0)

    def is_cooled_down(self, provider: str, key: str) -> bool:
        if not key:
            return False
        fp = self._fingerprint(provider, key)
        now = time.monotonic()
        expiry = self._cooldowns.get(fp)
        if expiry is None:
            return False
        if now >= expiry:
            self._cooldowns.pop(fp, None)
            return False
        return True

    def _prune(self, now: float) -> None:
        expired = [k for k, v in self._cooldowns.items() if now >= v]
        for k in expired:
            self._cooldowns.pop(k, None)


# ---------------------------------------------------------------------------
# LLM Gateway
# ---------------------------------------------------------------------------
class LLMGateway:
    """Provider-neutral LLM gateway managing capability selection, ordering, and safe failover."""

    def __init__(self):
        self.cooldown_tracker = CooldownTracker()

    def resolve_effective_preference(
        self,
        req: GenerationRequest,
        creds: Optional[GenerationCredentials] = None,
    ) -> ProviderPreference:
        pref = req.preference or (creds.provider_preference if creds else None) or settings.LLM_PROVIDER
        if pref not in ("auto", "groq", "gemini"):
            return "auto"
        return pref

    def resolve_keys(
        self,
        creds: Optional[GenerationCredentials] = None,
    ) -> Tuple[Optional[str], Optional[str]]:
        gemini_key = (creds.gemini_api_key if creds and creds.gemini_api_key else None) or settings.GEMINI_API_KEY or None
        groq_key = (creds.groq_api_key if creds and creds.groq_api_key else None) or settings.GROQ_API_KEY or None
        return gemini_key, groq_key

    def _determine_plan(
        self,
        req: GenerationRequest,
        pref: ProviderPreference,
        gemini_key: Optional[str],
        groq_key: Optional[str],
    ) -> List[Tuple[ProviderName, str, str]]:
        """Return ordered list of (provider_name, model_name, api_key) to attempt."""
        # 1. Multimodal requests are strictly Gemini-only
        if req.image_bytes is not None:
            if pref == "groq":
                raise LLMCapabilityError(
                    "Groq does not support multimodal image analysis in this deployment. "
                    "Switch provider preference to 'gemini' or 'auto'."
                )
            if not gemini_key:
                raise LLMAuthenticationError("Gemini API key is required for multimodal image analysis.")
            model = settings.DEFAULT_PRO_MODEL if req.use_high_quality else settings.DEFAULT_FLASH_MODEL
            return [("gemini", model, gemini_key)]

        # 2. Explicit provider selection
        if pref == "groq":
            if not groq_key:
                raise LLMAuthenticationError("Groq provider was explicitly selected, but no Groq API key is configured.")
            return [("groq", settings.GROQ_MODEL, groq_key)]

        if pref == "gemini":
            if not gemini_key:
                raise LLMAuthenticationError("Gemini provider was explicitly selected, but no Gemini API key is configured.")
            model = settings.DEFAULT_PRO_MODEL if req.use_high_quality else settings.DEFAULT_FLASH_MODEL
            return [("gemini", model, gemini_key)]

        # 3. Auto mode for text generation:
        # Order Groq primary when Groq key is available and not in active cooldown.
        plan: List[Tuple[ProviderName, str, str]] = []
        gemini_model = settings.DEFAULT_PRO_MODEL if req.use_high_quality else settings.DEFAULT_FLASH_MODEL

        groq_usable = bool(groq_key and not self.cooldown_tracker.is_cooled_down("groq", groq_key))
        if groq_usable:
            plan.append(("groq", settings.GROQ_MODEL, groq_key))  # type: ignore[arg-type]
            if gemini_key:
                plan.append(("gemini", gemini_model, gemini_key))
        elif groq_key and not gemini_key:
            # Only Groq key exists, even if cooling down
            plan.append(("groq", settings.GROQ_MODEL, groq_key))
        elif gemini_key:
            # Gemini primary (either no Groq key, or Groq is cooling down and Gemini is available)
            plan.append(("gemini", gemini_model, gemini_key))
            if groq_key:
                plan.append(("groq", settings.GROQ_MODEL, groq_key))

        if not plan:
            raise LLMAuthenticationError("No LLM API keys configured. Configure a Groq or Gemini API key in Settings.")

        return plan

    async def generate_text(
        self,
        req: GenerationRequest,
        creds: Optional[GenerationCredentials] = None,
    ) -> GenerationResult:
        """Execute non-streaming text generation across primary provider with safe failover."""
        pref = self.resolve_effective_preference(req, creds)
        gemini_key, groq_key = self.resolve_keys(creds)
        plan = self._determine_plan(req, pref, gemini_key, groq_key)

        timeout_s = req.timeout or settings.LLM_REQUEST_TIMEOUT_S
        deadline = time.monotonic() + timeout_s
        start_time = time.monotonic()

        attempted_providers: List[str] = []
        fallback_used = False
        fallback_reason: Optional[str] = None

        for idx, (provider, model, key) in enumerate(plan):
            is_fallback = idx > 0
            if is_fallback:
                fallback_used = True
            attempted_providers.append(provider)

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LLMTimeoutError(
                    f"LLM request exceeded total deadline of {timeout_s:.1f}s.",
                    attempted_providers=attempted_providers,
                )

            call_timeout = min(remaining, settings.GROQ_READ_TIMEOUT_S if provider == "groq" else timeout_s)

            try:
                if provider == "groq":
                    text = await groq_service.generate_text(
                        prompt=req.prompt,
                        system_instruction=req.system_instruction,
                        model=model,
                        api_key=key,
                        output_schema=req.output_schema,
                        json_object_mode=req.json_object_mode,
                        timeout=call_timeout,
                        max_tokens=req.max_output_tokens,
                    )
                else:
                    # Gemini service handles its internal retry and model fallback
                    text = await gemini_service.generate_text(
                        prompt=req.prompt,
                        system_instruction=req.system_instruction,
                        use_pro=req.use_high_quality,
                        override_key=key,
                        image_bytes=req.image_bytes,
                        mime_type=req.mime_type,
                        timeout=call_timeout,
                    )

                elapsed_ms = (time.monotonic() - start_time) * 1000.0
                logger.info(
                    "LLM task '%s' succeeded via provider=%s model=%s elapsed=%.1fms (fallback_used=%s)",
                    req.task_name,
                    provider,
                    model,
                    elapsed_ms,
                    fallback_used,
                )
                return GenerationResult(
                    text=text,
                    provider=provider,
                    model=model,
                    fallback_used=fallback_used,
                    fallback_reason=fallback_reason,
                    elapsed_ms=elapsed_ms,
                )

            except (GroqAuthenticationError, GeminiServiceError) as exc:
                # Do NOT failover on authentication failure (401/403)
                if isinstance(exc, (GroqAuthenticationError, GeminiAuthenticationError)) or getattr(exc, "code", "") == "AUTH_FAILURE":
                    logger.warning("Authentication failure on provider %s for task '%s'. No failover permitted.", provider, req.task_name)
                    raise LLMAuthenticationError(
                        f"Authentication failed for provider '{provider}'. Check your API key in Settings.",
                        attempted_providers=attempted_providers,
                    ) from exc
                # If GeminiServiceError is not auth, treat according to its classification below
                if isinstance(exc, GeminiServiceError):
                    code = getattr(exc, "code", "")
                    if code == "INVALID_REQUEST":
                        raise LLMInvalidRequestError(str(exc), attempted_providers=attempted_providers) from exc
                    if isinstance(exc, (GeminiQuotaExhaustedError, GeminiRateLimitError)):
                        self.cooldown_tracker.set_cooldown(provider, key, 45.0)
                        fallback_reason = f"Gemini quota/rate limit: {exc}"
                        logger.warning("Gemini quota/rate limit on task '%s'. Attempting alternate provider if available.", req.task_name)
                        if is_fallback or idx == len(plan) - 1:
                            raise LLMQuotaExhaustedError(str(exc), attempted_providers=attempted_providers) from exc
                        continue
                    if isinstance(exc, GeminiModelUnavailableError):
                        fallback_reason = f"Gemini model unavailable: {exc}"
                        if is_fallback or idx == len(plan) - 1:
                            raise LLMModelUnavailableError(str(exc), attempted_providers=attempted_providers) from exc
                        continue
                    # Generic Gemini service error
                    fallback_reason = f"Gemini error: {exc}"
                    if is_fallback or idx == len(plan) - 1:
                        raise LLMServiceError(f"Gemini service error: {exc}", attempted_providers=attempted_providers) from exc
                    continue

            except GroqInvalidRequestError as exc:
                logger.warning("Invalid request to Groq for task '%s': %s", req.task_name, exc.message)
                raise LLMInvalidRequestError(exc.message, attempted_providers=attempted_providers) from exc

            except (GroqRateLimitError, GroqCapacityError) as exc:
                cooldown_dur = exc.retry_after if exc.retry_after is not None else 30.0
                self.cooldown_tracker.set_cooldown("groq", key, cooldown_dur)
                err_type = "rate limit" if isinstance(exc, GroqRateLimitError) else "capacity exhaustion"
                fallback_reason = f"Groq {err_type}: {exc.message}"
                logger.warning("Groq %s on task '%s'. Cooling down for %.1fs. Failing over.", err_type, req.task_name, cooldown_dur)
                if is_fallback or idx == len(plan) - 1:
                    if isinstance(exc, GroqRateLimitError):
                        raise LLMRateLimitError(exc.message, attempted_providers=attempted_providers, retry_after=exc.retry_after) from exc
                    raise LLMServiceError(f"Groq {err_type}: {exc.message}", attempted_providers=attempted_providers) from exc
                continue

            except (GroqServiceError, GroqTimeoutError) as exc:
                fallback_reason = f"Groq failure: {exc}"
                logger.warning("Groq failure on task '%s': %s. Failing over.", req.task_name, exc)
                if is_fallback or idx == len(plan) - 1:
                    raise LLMServiceError(f"Groq failure: {exc}", attempted_providers=attempted_providers) from exc
                continue

            except asyncio.CancelledError:
                logger.info("LLM task '%s' cancelled. Not failing over.", req.task_name)
                raise

            except Exception as exc:
                fallback_reason = f"Unexpected error on {provider}: {exc}"
                logger.exception("Unexpected error on %s for task '%s'", provider, req.task_name)
                if is_fallback or idx == len(plan) - 1:
                    raise LLMServiceError(f"LLM generation failed: {exc}", attempted_providers=attempted_providers) from exc
                continue

        raise LLMServiceError(
            "All configured LLM providers failed to complete the request.",
            attempted_providers=attempted_providers,
        )

    async def generate_stream(
        self,
        req: GenerationRequest,
        creds: Optional[GenerationCredentials] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Stream completion tokens with first-token establishment phase.

        Yields dictionaries:
          - First: {"type": "meta", "provider": ..., "model": ..., "fallback_used": ...}
          - Subsequent: {"text": chunk}
          - On terminal stream error: {"error": safe_message}
        """
        pref = self.resolve_effective_preference(req, creds)
        gemini_key, groq_key = self.resolve_keys(creds)
        plan = self._determine_plan(req, pref, gemini_key, groq_key)

        timeout_s = req.timeout or settings.LLM_REQUEST_TIMEOUT_S
        first_content_timeout = req.first_content_timeout or settings.LLM_FIRST_CONTENT_TIMEOUT_S
        first_content_timeout = min(first_content_timeout, timeout_s)
        start_time = time.monotonic()
        attempted_providers: List[str] = []

        first_token: Optional[str] = None
        selected_provider: Optional[ProviderName] = None
        selected_model: Optional[str] = None
        fallback_used = False
        stream_gen = None

        # --- Phase 1: First-token establishment phase (failover allowed in auto mode) ---
        for idx, (provider, model, key) in enumerate(plan):
            is_fallback = idx > 0
            attempted_providers.append(provider)
            provider_start = time.monotonic()
            first_token_deadline = provider_start + first_content_timeout
            stream = None

            try:
                if provider == "groq":
                    stream = groq_service.generate_stream(
                        prompt=req.prompt,
                        system_instruction=req.system_instruction,
                        model=model,
                        api_key=key,
                        timeout=timeout_s,
                        max_tokens=req.max_output_tokens,
                    )
                else:
                    stream = gemini_service.generate_stream(
                        prompt=req.prompt,
                        system_instruction=req.system_instruction,
                        use_pro=req.use_high_quality,
                        override_key=key,
                    )

                stream_iter = stream.__aiter__()
                # Attempt to retrieve the first non-empty content token within the absolute deadline
                while True:
                    time_remaining = first_token_deadline - time.monotonic()
                    if time_remaining <= 0:
                        raise LLMTimeoutError(
                            f"Provider '{provider}' exceeded first-content deadline ({first_content_timeout:.1f}s).",
                            attempted_providers=attempted_providers,
                        )

                    chunk = await asyncio.wait_for(stream_iter.__anext__(), timeout=time_remaining)
                    if chunk and isinstance(chunk, str) and chunk.strip():
                        first_token = chunk
                        selected_provider = provider
                        selected_model = model
                        fallback_used = is_fallback
                        stream_gen = stream_iter
                        ttft_ms = (time.monotonic() - provider_start) * 1000
                        logger.info(
                            "First token established: task=%s provider=%s model=%s ttft_ms=%.1f fallback_used=%s",
                            req.task_name,
                            provider,
                            model,
                            ttft_ms,
                            fallback_used,
                        )
                        break

                if first_token is not None:
                    break

            except asyncio.CancelledError:
                logger.info("Active stream setup for '%s' cancelled by client.", req.task_name)
                if stream and hasattr(stream, "aclose"):
                    try:
                        await stream.aclose()
                    except Exception:
                        pass
                raise
            except (GroqAuthenticationError, GeminiAuthenticationError) as exc:
                logger.warning("Authentication failure on %s for '%s': %s", provider, req.task_name, exc)
                yield {"error": f"Authentication failed for provider '{provider}'. Check your key in Settings."}
                return
            except GroqInvalidRequestError as exc:
                logger.warning("Invalid request on %s for '%s': %s", provider, req.task_name, exc)
                yield {"error": f"Invalid request for provider '{provider}': {exc.message}"}
                return
            except (asyncio.TimeoutError, LLMTimeoutError, GroqTimeoutError) as exc:
                logger.warning("First-content timeout on %s for '%s' after %.1fs.", provider, req.task_name, first_content_timeout)
                if stream and hasattr(stream, "aclose"):
                    try:
                        await stream.aclose()
                    except Exception:
                        pass
                if pref != "auto" or is_fallback or idx == len(plan) - 1:
                    yield {"error": f"Provider '{provider}' timed out waiting for answer content."}
                    return
                continue
            except (GroqEmptyResponseError, StopAsyncIteration) as exc:
                logger.warning("Provider %s stream completed without content for '%s'.", provider, req.task_name)
                if stream and hasattr(stream, "aclose"):
                    try:
                        await stream.aclose()
                    except Exception:
                        pass
                if pref != "auto" or is_fallback or idx == len(plan) - 1:
                    yield {"error": "Stream completed without answer content from provider."}
                    return
                continue
            except (GroqRateLimitError, GroqCapacityError) as exc:
                self.cooldown_tracker.set_cooldown("groq", key, getattr(exc, "retry_after", None) or 30.0)
                if pref != "auto" or is_fallback or idx == len(plan) - 1:
                    yield {"error": f"Groq capacity or rate limit reached: {exc.message}"}
                    return
                continue
            except (GeminiRateLimitError, GeminiQuotaExhaustedError) as exc:
                self.cooldown_tracker.set_cooldown("gemini", key, getattr(exc, "retry_after", None) or 30.0)
                if pref != "auto" or is_fallback or idx == len(plan) - 1:
                    yield {"error": f"Gemini quota or rate limit reached: {exc.message}"}
                    return
                continue
            except Exception as exc:
                logger.warning("Stream pre-token failure on %s for '%s': %s", provider, req.task_name, exc)
                if stream and hasattr(stream, "aclose"):
                    try:
                        await stream.aclose()
                    except Exception:
                        pass
                if pref != "auto" or is_fallback or idx == len(plan) - 1:
                    yield {"error": f"LLM stream connection failed: {exc}"}
                    return
                continue

        if first_token is None or selected_provider is None or stream_gen is None:
            yield {"error": "All configured LLM providers failed before stream initialization."}
            return

        # --- Phase 2: Commit provider and emit metadata frame ---
        yield {
            "type": "meta",
            "provider": selected_provider,
            "model": selected_model,
            "fallback_used": fallback_used,
        }
        yield {"text": first_token}

        # --- Phase 3: Stream remaining content — strictly NO failover after first token ---
        try:
            async for chunk in stream_gen:
                if chunk:
                    yield {"text": chunk}
        except asyncio.CancelledError:
            logger.info("Active stream '%s' cancelled by client.", req.task_name)
            raise
        except Exception as exc:
            logger.warning("Stream interrupted after initial output on provider %s: %s", selected_provider, exc)
            yield {"error": f"Connection to {selected_provider} interrupted while streaming."}


llm_gateway = LLMGateway()
