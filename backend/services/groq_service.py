import asyncio
import json
import logging
import math
import os
import re
import time
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx

from backend.config import settings

logger = logging.getLogger(__name__)

# Fixed official HTTPS API endpoint — never user-configurable to prevent key redirection.
GROQ_BASE_URL = "https://api.groq.com/openai/v1/chat/completions"


# ---------------------------------------------------------------------------
# Groq Exceptions
# ---------------------------------------------------------------------------
class GroqServiceError(Exception):
    """Base exception for all Groq provider errors."""

    def __init__(self, message: str, status_code: Optional[int] = None, retry_after: Optional[float] = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.retry_after = retry_after


class GroqAuthenticationError(GroqServiceError):
    """Raised on HTTP 401 or 403 (invalid API key or unauthorized)."""


class GroqInvalidRequestError(GroqServiceError):
    """Raised on HTTP 400 or 422 (malformed request, unsupported parameter, or invalid schema)."""


class GroqRateLimitError(GroqServiceError):
    """Raised on HTTP 429 (rate limit or quota exhausted)."""


class GroqCapacityError(GroqServiceError):
    """Raised on HTTP 498 or server overload."""


class GroqModelUnavailableError(GroqServiceError):
    """Raised on HTTP 404 (model not found or deprecated)."""


class GroqTimeoutError(GroqServiceError):
    """Raised when request times out."""


class GroqEmptyResponseError(GroqServiceError):
    """Raised when Groq completes without non-whitespace final answer content."""


# ---------------------------------------------------------------------------
# Groq Service Adapter
# ---------------------------------------------------------------------------
class GroqService:
    """HTTP adapter for Groq chat completions using a shared httpx.AsyncClient."""

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None
        self._client_lock = asyncio.Lock()

    async def get_client(self) -> httpx.AsyncClient:
        """Get or initialize the shared reusable httpx client."""
        if self._client is not None and not self._client.is_closed:
            return self._client
        async with self._client_lock:
            if self._client is None or self._client.is_closed:
                limits = httpx.Limits(max_keepalive_connections=10, max_connections=20)
                timeout = httpx.Timeout(
                    connect=settings.GROQ_CONNECT_TIMEOUT_S,
                    read=settings.GROQ_READ_TIMEOUT_S,
                    write=10.0,
                    pool=5.0,
                )
                self._client = httpx.AsyncClient(limits=limits, timeout=timeout)
            return self._client

    async def aclose(self) -> None:
        """Close the shared client during application lifespan shutdown."""
        async with self._client_lock:
            if self._client is not None and not self._client.is_closed:
                await self._client.aclose()
                self._client = None

    @staticmethod
    def _parse_retry_after(headers: httpx.Headers) -> Optional[float]:
        """Extract retry-after seconds from response headers."""
        raw = headers.get("retry-after")
        if not raw:
            return None
        try:
            val = float(raw)
            if val > 0 and math.isfinite(val):
                return val
        except (ValueError, TypeError):
            pass
        return None

    def _classify_error(self, status_code: int, error_text: str, headers: httpx.Headers) -> GroqServiceError:
        """Map Groq HTTP error responses into categorized exceptions without exposing secrets."""
        retry_after = self._parse_retry_after(headers)
        safe_detail = "Groq request failed."
        try:
            parsed = json.loads(error_text)
            if isinstance(parsed, dict) and "error" in parsed:
                err_obj = parsed["error"]
                if isinstance(err_obj, dict):
                    safe_detail = err_obj.get("message") or safe_detail
                elif isinstance(err_obj, str):
                    safe_detail = err_obj
        except Exception:
            pass

        # Sanitize any accidental key leak in error text
        safe_detail = re.sub(r"gsk_[a-zA-Z0-9_-]+", "[REDACTED_KEY]", str(safe_detail))

        if status_code in (401, 403):
            return GroqAuthenticationError(
                f"Groq authentication failed: {safe_detail}",
                status_code=status_code,
            )
        if status_code == 404:
            return GroqModelUnavailableError(
                f"Groq model unavailable or not found: {safe_detail}",
                status_code=status_code,
            )
        if status_code in (400, 422):
            return GroqInvalidRequestError(
                f"Invalid Groq request: {safe_detail}",
                status_code=status_code,
            )
        if status_code == 429:
            msg = f"Groq rate limit exceeded: {safe_detail}"
            if retry_after is not None:
                msg += f" (retry after {retry_after:.1f}s)"
            return GroqRateLimitError(msg, status_code=status_code, retry_after=retry_after)
        if status_code in (498, 503):
            return GroqCapacityError(
                f"Groq capacity temporarily exhausted: {safe_detail}",
                status_code=status_code,
                retry_after=retry_after,
            )
        if 500 <= status_code < 600 or status_code == 408:
            return GroqServiceError(
                f"Groq service error (HTTP {status_code}): {safe_detail}",
                status_code=status_code,
                retry_after=retry_after,
            )
        return GroqServiceError(
            f"Groq API error (HTTP {status_code}): {safe_detail}",
            status_code=status_code,
            retry_after=retry_after,
        )

    @staticmethod
    def _supports_include_reasoning(model: str) -> bool:
        """Models known to support include_reasoning parameter without error."""
        m = model.lower()
        return "gpt-oss" in m or "deepseek" in m or "qwen" in m

    async def generate_text(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: Optional[float] = None,
        max_tokens: Optional[int] = None,
        output_schema: Optional[Dict[str, Any]] = None,
        json_object_mode: bool = False,
    ) -> str:
        """Generate complete text response from Groq. Returns strictly final answer content."""
        effective_key = api_key or settings.GROQ_API_KEY
        if not effective_key:
            raise GroqAuthenticationError("No Groq API key configured. Provide one in Settings or environment.")

        effective_model = model or settings.GROQ_MODEL
        messages: List[Dict[str, str]] = []
        if system_instruction and system_instruction.strip():
            messages.append({"role": "system", "content": system_instruction.strip()})
        messages.append({"role": "user", "content": prompt})

        payload: Dict[str, Any] = {
            "model": effective_model,
            "messages": messages,
            "temperature": 0.2 if output_schema or json_object_mode else 0.7,
            "max_tokens": max_tokens or settings.LLM_MAX_OUTPUT_TOKENS,
        }
        if self._supports_include_reasoning(effective_model):
            payload["include_reasoning"] = False

        # Apply strict JSON Schema if requested, or JSON Object mode
        if output_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "structured_response",
                    "strict": True,
                    "schema": output_schema,
                },
            }
        elif json_object_mode:
            payload["response_format"] = {"type": "json_object"}

        headers = {
            "Authorization": f"Bearer {effective_key}",
            "Content-Type": "application/json",
        }

        read_timeout = timeout if timeout is not None else settings.GROQ_READ_TIMEOUT_S
        req_timeout = httpx.Timeout(
            connect=settings.GROQ_CONNECT_TIMEOUT_S,
            read=read_timeout,
            write=10.0,
            pool=5.0,
        )

        client = await self.get_client()
        try:
            response = await client.post(
                GROQ_BASE_URL,
                headers=headers,
                json=payload,
                timeout=req_timeout,
            )
        except httpx.TimeoutException as exc:
            raise GroqTimeoutError(f"Groq request timed out after {read_timeout:.1f}s.") from exc
        except (httpx.ConnectError, httpx.NetworkError, ConnectionError) as exc:
            raise GroqServiceError(f"Groq network connection failure: {exc}") from exc

        if not 200 <= response.status_code < 300:
            error_text = response.text
            raise self._classify_error(response.status_code, error_text, response.headers)

        try:
            data = response.json()
        except Exception as exc:
            raise GroqServiceError("Groq returned malformed non-JSON response body.") from exc

        choices = data.get("choices")
        if not choices or not isinstance(choices, list):
            raise GroqServiceError("Groq response did not contain valid completion choices.")

        first_choice = choices[0]
        message_obj = first_choice.get("message", {})
        content = message_obj.get("content")

        if content is None or (isinstance(content, str) and not content.strip()):
            raise GroqEmptyResponseError("Groq returned empty final answer content.")

        return str(content).strip()

    async def generate_stream(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> AsyncGenerator[str, None]:
        """Stream completion tokens using Server-Sent Events (SSE)."""
        effective_key = api_key or settings.GROQ_API_KEY
        if not effective_key:
            raise GroqAuthenticationError("No Groq API key configured. Provide one in Settings or environment.")

        effective_model = model or settings.GROQ_MODEL
        messages: List[Dict[str, str]] = []
        if system_instruction and system_instruction.strip():
            messages.append({"role": "system", "content": system_instruction.strip()})
        messages.append({"role": "user", "content": prompt})

        payload: Dict[str, Any] = {
            "model": effective_model,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": max_tokens or settings.LLM_MAX_OUTPUT_TOKENS,
            "stream": True,
        }
        if self._supports_include_reasoning(effective_model):
            payload["include_reasoning"] = False

        headers = {
            "Authorization": f"Bearer {effective_key}",
            "Content-Type": "application/json",
        }

        read_timeout = timeout if timeout is not None else settings.GROQ_READ_TIMEOUT_S
        req_timeout = httpx.Timeout(
            connect=settings.GROQ_CONNECT_TIMEOUT_S,
            read=read_timeout,
            write=10.0,
            pool=5.0,
        )

        client = await self.get_client()

        start_time = time.monotonic()
        content_frames_count = 0
        non_content_frames_count = 0
        ttft_ms: Optional[float] = None
        finish_reason: Optional[str] = None

        try:
            async with client.stream("POST", GROQ_BASE_URL, headers=headers, json=payload, timeout=req_timeout) as response:
                # Validate non-2xx status before reading stream frames
                if not 200 <= response.status_code < 300:
                    error_bytes = await response.aread()
                    error_text = error_bytes.decode("utf-8", errors="replace")
                    raise self._classify_error(response.status_code, error_text, response.headers)

                buffer = ""
                async for chunk in response.aiter_text():
                    buffer += chunk
                    lines = buffer.split("\n")
                    buffer = lines.pop()  # Keep incomplete tail in buffer

                    for raw_line in lines:
                        line = raw_line.strip()
                        if not line or line.startswith(":"):
                            non_content_frames_count += 1
                            continue
                        if line.startswith("data:"):
                            data_content = line[5:].strip()
                            if data_content == "[DONE]":
                                if content_frames_count == 0:
                                    logger.warning(
                                        "Groq stream completed with [DONE] but zero content frames: model=%s non_content=%d finish_reason=%s",
                                        effective_model,
                                        non_content_frames_count,
                                        finish_reason,
                                    )
                                    raise GroqEmptyResponseError(
                                        f"Groq stream completed without final answer content (finish_reason={finish_reason or 'none'})."
                                    )
                                logger.info(
                                    "Groq stream finished successfully: model=%s content_frames=%d non_content_frames=%d finish_reason=%s ttft_ms=%s elapsed_ms=%.1f",
                                    effective_model,
                                    content_frames_count,
                                    non_content_frames_count,
                                    finish_reason or "none",
                                    f"{ttft_ms:.1f}" if ttft_ms is not None else "none",
                                    (time.monotonic() - start_time) * 1000,
                                )
                                return
                            try:
                                parsed = json.loads(data_content)
                            except Exception as exc:
                                raise GroqServiceError(f"Malformed streaming SSE JSON frame: {line}") from exc

                            choices = parsed.get("choices", [])
                            if not choices:
                                non_content_frames_count += 1
                                continue

                            fr = choices[0].get("finish_reason")
                            if fr:
                                finish_reason = fr

                            delta = choices[0].get("delta", {})
                            token = delta.get("content")
                            if token and isinstance(token, str) and token != "":
                                if ttft_ms is None and token.strip():
                                    ttft_ms = (time.monotonic() - start_time) * 1000
                                content_frames_count += 1
                                yield token
                            else:
                                non_content_frames_count += 1

                if content_frames_count == 0:
                    raise GroqEmptyResponseError(
                        f"Groq stream closed without final answer content (finish_reason={finish_reason or 'none'})."
                    )
        except httpx.TimeoutException as exc:
            raise GroqTimeoutError(f"Groq streaming timed out after {read_timeout:.1f}s.") from exc
        except (httpx.ConnectError, httpx.NetworkError, ConnectionError) as exc:
            raise GroqServiceError(f"Groq streaming network connection failure: {exc}") from exc
        except asyncio.CancelledError:
            raise

    async def test_connection(self, api_key: str, model: Optional[str] = None, timeout: float = 10.0) -> Dict[str, Any]:
        """Probe Groq connection and model availability without logging or persisting keys."""
        if not api_key or not api_key.strip():
            return {
                "success": False,
                "model": model or settings.GROQ_MODEL,
                "message": "Enter an API key before testing.",
            }
        effective_model = model or settings.GROQ_MODEL
        try:
            await self.generate_text(
                prompt="Respond with PONG.",
                model=effective_model,
                api_key=api_key.strip(),
                max_tokens=300,
                timeout=timeout,
            )
            return {
                "success": True,
                "model": effective_model,
                "message": f"Connection valid. Groq model '{effective_model}' responded successfully.",
            }
        except GroqEmptyResponseError as exc:
            return {
                "success": False,
                "model": effective_model,
                "message": f"Groq model '{effective_model}' connected but returned no final answer content.",
            }
        except GroqAuthenticationError as exc:
            return {
                "success": False,
                "model": effective_model,
                "message": f"Authentication failed: {exc.message}",
            }
        except GroqModelUnavailableError as exc:
            return {
                "success": False,
                "model": effective_model,
                "message": f"Model unavailable: {exc.message}",
            }
        except GroqRateLimitError as exc:
            return {
                "success": False,
                "model": effective_model,
                "message": f"Rate limit reached on Groq account: {exc.message}",
            }
        except Exception as exc:
            return {
                "success": False,
                "model": effective_model,
                "message": f"Connection probe failed: {exc}",
            }


groq_service = GroqService()
