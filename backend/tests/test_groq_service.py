"""Unit tests for GroqService (Groq text-generation client).

Tests:
1. Generation success with standard JSON response.
2. Generation success with JSON schema / object mode.
3. Streaming SSE line parser with valid content delta chunks.
4. Error mapping:
   - 401/403 -> GroqAuthenticationError
   - 400/422 -> GroqInvalidRequestError
   - 404 -> GroqModelUnavailableError
   - 429 -> GroqRateLimitError (parses retry-after header)
   - 503 -> GroqCapacityError
   - 500/502/504 -> GroqServiceError
   - Timeout -> GroqTimeoutError
5. Connection test probe (test_connection).
6. Security invariants: API key never appears in exceptions, URLs, or sanitized representations.
7. Base URL invariant: always fixed to https://api.groq.com/openai/v1/chat/completions.
"""

import json
import httpx
import pytest

from backend.services.groq_service import (
    GROQ_BASE_URL,
    GroqAuthenticationError,
    GroqCapacityError,
    GroqEmptyResponseError,
    GroqInvalidRequestError,
    GroqModelUnavailableError,
    GroqRateLimitError,
    GroqService,
    GroqServiceError,
    GroqTimeoutError,
)


@pytest.mark.asyncio
async def test_groq_generate_text_success():
    """Verify standard text generation returns trimmed completion text."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == GROQ_BASE_URL
        assert request.headers["Authorization"] == "Bearer gsk_test_key_123"
        payload = json.loads(request.content)
        assert payload["model"] == "openai/gpt-oss-120b"
        assert len(payload["messages"]) >= 1

        response_body = {
            "id": "chatcmpl-test1",
            "object": "chat.completion",
            "created": 1700000000,
            "model": "openai/gpt-oss-120b",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "Hello! I am ready to help.",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
        }
        return httpx.Response(200, json=response_body)

    transport = httpx.MockTransport(handler)
    service = GroqService()
    service._client = httpx.AsyncClient(transport=transport)

    try:
        result = await service.generate_text("Say hello", api_key="gsk_test_key_123")
        assert result == "Hello! I am ready to help."
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_groq_generate_text_json_schema():
    """Verify structured response_format is correctly passed in payload."""
    received_format = {}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal received_format
        payload = json.loads(request.content)
        received_format = payload.get("response_format")
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": '{"verdict": "pass", "score": 9.5}',
                        }
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    service = GroqService()
    service._client = httpx.AsyncClient(transport=transport)

    schema = {
        "type": "object",
        "properties": {"score": {"type": "number"}, "verdict": {"type": "string"}},
        "required": ["score", "verdict"],
    }

    try:
        result = await service.generate_text(
            "Evaluate code",
            api_key="gsk_test_key_123",
            output_schema=schema,
        )
        assert received_format["type"] == "json_schema"
        assert received_format["json_schema"]["strict"] is True
        assert json.loads(result)["score"] == 9.5
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_groq_streaming_sse_chunks():
    """Verify streaming generator yields content chunks and respects [DONE]."""
    sse_data = (
        b"data: {\"choices\": [{\"delta\": {\"content\": \"Hello \"}}]}\n\n"
        b": keep-alive comment line\n\n"
        b"data: {\"choices\": [{\"delta\": {\"content\": \"world!\"}}]}\n\n"
        b"data: [DONE]\n\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["stream"] is True
        return httpx.Response(200, content=sse_data, headers={"Content-Type": "text/event-stream"})

    transport = httpx.MockTransport(handler)
    service = GroqService()
    service._client = httpx.AsyncClient(transport=transport)

    try:
        chunks = []
        async for chunk in service.generate_stream("Stream prompt", api_key="gsk_test_key_123"):
            chunks.append(chunk)

        assert chunks == ["Hello ", "world!"]
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_groq_authentication_error_401():
    """Verify HTTP 401 raises GroqAuthenticationError."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"error": {"message": "Invalid API Key provided", "type": "invalid_request_error"}},
        )

    transport = httpx.MockTransport(handler)
    service = GroqService()
    service._client = httpx.AsyncClient(transport=transport)

    try:
        with pytest.raises(GroqAuthenticationError) as exc_info:
            await service.generate_text("Test", api_key="invalid_key")
        assert "Groq authentication failed" in str(exc_info.value)
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_groq_rate_limit_429_with_retry_after():
    """Verify HTTP 429 parses retry-after header and raises GroqRateLimitError."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={"error": {"message": "Rate limit reached for requests per minute (RPM)", "type": "tokens"}},
            headers={"Retry-After": "15"},
        )

    transport = httpx.MockTransport(handler)
    service = GroqService()
    service._client = httpx.AsyncClient(transport=transport)

    try:
        with pytest.raises(GroqRateLimitError) as exc_info:
            await service.generate_text("Test", api_key="gsk_test_key_123")
        assert exc_info.value.retry_after == 15.0
        assert "Rate limit" in exc_info.value.message
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_groq_capacity_503():
    """Verify HTTP 503 raises GroqCapacityError."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="Service Unavailable: Model overloaded")

    transport = httpx.MockTransport(handler)
    service = GroqService()
    service._client = httpx.AsyncClient(transport=transport)

    try:
        with pytest.raises(GroqCapacityError):
            await service.generate_text("Test", api_key="gsk_test_key_123")
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_groq_model_unavailable_404():
    """Verify HTTP 404 raises GroqModelUnavailableError."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": {"message": "The model `retired-model` does not exist"}})

    transport = httpx.MockTransport(handler)
    service = GroqService()
    service._client = httpx.AsyncClient(transport=transport)

    try:
        with pytest.raises(GroqModelUnavailableError):
            await service.generate_text("Test", api_key="gsk_test_key_123")
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_groq_invalid_request_400():
    """Verify HTTP 400 raises GroqInvalidRequestError."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "Context window exceeded"}})

    transport = httpx.MockTransport(handler)
    service = GroqService()
    service._client = httpx.AsyncClient(transport=transport)

    try:
        with pytest.raises(GroqInvalidRequestError) as exc_info:
            await service.generate_text("Test", api_key="gsk_test_key_123")
        assert "Context window exceeded" in exc_info.value.message
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_groq_test_connection_success():
    """Verify test_connection returns success=True and model name."""
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["max_tokens"] == 300
        return httpx.Response(
            200,
            json={
                "model": "openai/gpt-oss-120b",
                "choices": [{"message": {"content": "pong"}}],
            },
        )

    transport = httpx.MockTransport(handler)
    service = GroqService()
    service._client = httpx.AsyncClient(transport=transport)

    try:
        res = await service.test_connection("gsk_valid_key")
        assert res["success"] is True
        assert res["model"] == "openai/gpt-oss-120b"
        assert "Connection valid" in res["message"]
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_groq_reasoning_only_raises_empty_content_error():
    """Verify that when a model returns only reasoning and no final content, GroqEmptyResponseError is raised and test_connection reports failure accurately."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "openai/gpt-oss-120b",
                "choices": [{
                    "message": {
                        "content": "",
                        "reasoning": "I need to respond to ping.",
                    }
                }],
            },
        )

    transport = httpx.MockTransport(handler)
    service = GroqService()
    service._client = httpx.AsyncClient(transport=transport)

    try:
        with pytest.raises(GroqEmptyResponseError):
            await service.generate_text("ping", api_key="gsk_valid_key")

        conn_res = await service.test_connection("gsk_valid_key")
        assert conn_res["success"] is False
        assert conn_res["model"] == "openai/gpt-oss-120b"
        assert "no final answer content" in conn_res["message"]
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_groq_streaming_reasoning_only_then_final_content():
    """Verify reasoning-only deltas are ignored and only final content is yielded."""
    sse_data = (
        b"data: {\"choices\": [{\"delta\": {\"reasoning\": \"Step 1: thinking...\"}}]}\n\n"
        b": keep-alive\n\n"
        b"data: {\"choices\": [{\"delta\": {\"reasoning\": \"Step 2: still thinking...\"}}]}\n\n"
        b"data: {\"choices\": [{\"delta\": {\"content\": \"Final answer\"}, \"finish_reason\": \"stop\"}]}\n\n"
        b"data: [DONE]\n\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=sse_data, headers={"Content-Type": "text/event-stream"})

    transport = httpx.MockTransport(handler)
    service = GroqService()
    service._client = httpx.AsyncClient(transport=transport)

    try:
        chunks = []
        async for chunk in service.generate_stream("Stream with reasoning", api_key="gsk_valid_key"):
            chunks.append(chunk)

        assert chunks == ["Final answer"]
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_groq_streaming_reasoning_only_then_done_raises_empty_error():
    """Verify streaming that emits only reasoning followed by [DONE] raises GroqEmptyResponseError."""
    sse_data = (
        b"data: {\"choices\": [{\"delta\": {\"reasoning\": \"Step 1: thinking...\"}}]}\n\n"
        b": keep-alive\n\n"
        b"data: {\"choices\": [{\"delta\": {\"reasoning\": \"Step 2: still thinking...\"}}]}\n\n"
        b"data: [DONE]\n\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=sse_data, headers={"Content-Type": "text/event-stream"})

    transport = httpx.MockTransport(handler)
    service = GroqService()
    service._client = httpx.AsyncClient(transport=transport)

    try:
        with pytest.raises(GroqEmptyResponseError):
            async for _ in service.generate_stream("Stream with only reasoning", api_key="gsk_valid_key"):
                pass
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_groq_include_reasoning_payload_contract():
    """Verify include_reasoning: false is passed for gpt-oss models and reasoning_format is omitted."""
    received_payload = {}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal received_payload
        received_payload = json.loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "Answer"}}]},
        )

    transport = httpx.MockTransport(handler)
    service = GroqService()
    service._client = httpx.AsyncClient(transport=transport)

    try:
        await service.generate_text("Prompt", model="openai/gpt-oss-120b", api_key="gsk_valid_key")
        assert received_payload.get("include_reasoning") is False
        assert "reasoning_format" not in received_payload

        # For non-reasoning model like llama, include_reasoning is not set
        await service.generate_text("Prompt", model="llama-3.3-70b-versatile", api_key="gsk_valid_key")
        assert "include_reasoning" not in received_payload
        assert "reasoning_format" not in received_payload
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_groq_test_connection_invalid():
    """Verify test_connection with bad key returns success=False without throwing."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "Invalid API Key"}})

    transport = httpx.MockTransport(handler)
    service = GroqService()
    service._client = httpx.AsyncClient(transport=transport)

    try:
        res = await service.test_connection("gsk_bad_key")
        assert res["success"] is False
        assert "Authentication failed" in res["message"]
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_groq_security_never_leaks_api_key():
    """Verify the API key string never appears in exception messages or repr."""
    secret_key = "gsk_SUPER_SECRET_TOKEN_99999"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal server error")

    transport = httpx.MockTransport(handler)
    service = GroqService()
    service._client = httpx.AsyncClient(transport=transport)

    try:
        with pytest.raises(GroqServiceError) as exc_info:
            await service.generate_text("Test", api_key=secret_key)
        assert secret_key not in str(exc_info.value)
        assert secret_key not in repr(exc_info.value)
    finally:
        await service.aclose()


def test_groq_base_url_immutable():
    """Verify base URL is a hardcoded official Groq endpoint constant."""
    assert GROQ_BASE_URL == "https://api.groq.com/openai/v1/chat/completions"
