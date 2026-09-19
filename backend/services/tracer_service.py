import json
import logging
from typing import Dict, Any, List, Optional, AsyncGenerator
from backend.services.context_builder import context_builder
from backend.services.gemini_service import GeminiServiceError, gemini_service
from backend.services.llm_gateway import (
    llm_gateway,
    GenerationCredentials,
    GenerationRequest,
    LLMServiceError,
)
from backend.services.vector_store import vector_store

logger = logging.getLogger(__name__)

TRACER_SYSTEM_PROMPT = """
You are an expert autonomous static analysis code tracer agent.
Your task is to trace full execution flows (e.g. "What happens when a user logs in?" or "Trace form submission logic") across files, function calls, state updates, and external API requests in the given codebase.
Produce structured step-by-step hop sequences that guide the student through the execution flow like an interactive tour.

CRITICAL RELEVANCE AND GROUNDING RULES:
1. BEFORE TRACING: Validate if the query relates to any real function, feature, module, or user action detectable in the provided codebase snippets.
2. IF THE QUERY IS IRRELEVANT or off-topic (e.g. "I love sleeping", "what's the weather today", or unrelated non-code questions), DO NOT fabricate hops. Set "relevant": false, "total_hops": 0, "hops": [], and provide a helpful message explaining that it doesn't relate to anything in this codebase.
3. MID-TRACE STOPPING: Only produce legitimate hops grounded in actual functions/code present in the codebase. Do NOT invent fake files, functions, or hops to pad the trace. If execution ends or no further legitimate hop can be found, stop tracing immediately and set "next_call": "End of trace".
"""

class TracerService:
    async def trace_flow(
        self,
        repo_id: str,
        flow_query: str,
        override_key: Optional[str] = None,
        override_groq_key: Optional[str] = None,
        provider_preference: Optional[str] = None,
        credentials: Optional[GenerationCredentials] = None,
    ) -> Dict[str, Any]:
        # Search vector store for components related to query
        effective_key = override_key or (credentials.gemini_api_key if credentials else None)
        rag_results = await vector_store.search(repo_id, flow_query, top_k=6, override_key=effective_key)

        max_sim = max([r.get("similarity", 0.0) for r in rag_results]) if rag_results else 0.0
        has_exact_match = any(r.get("exact_match", False) for r in rag_results)

        # Pre-validation check: If no results or very low similarity without exact file match, decline immediately
        if not rag_results or (max_sim < 0.15 and not has_exact_match):
            logger.info(f"Query '{flow_query}' failed RAG relevance pre-check (max_sim={max_sim}).")
            return {
                "query": flow_query,
                "relevant": False,
                "message": "That doesn't seem to relate to anything in this codebase — try asking about a specific feature or action, like 'what happens when a user submits the form?'",
                "total_hops": 0,
                "hops": []
            }

        code_snippets = context_builder.build_context(rag_results, max_tokens=4000).context_text

        prompt = f"""
Query: "{flow_query}"

Relevant Codebase Snippets:
{code_snippets}

Task: First, determine if the Query relates to any actual function, feature, or execution flow in the provided codebase snippets.

If the query is IRRELEVANT or off-topic (e.g., nonsensical text, general chat, or asking about features not present in the code):
Return JSON:
{{
    "query": "{flow_query}",
    "relevant": false,
    "message": "That doesn't seem to relate to anything in this codebase — try asking about a specific feature or action, like 'what happens when a user submits the form?'",
    "total_hops": 0,
    "hops": []
}}

If the query IS RELEVANT, trace the exact step-by-step execution path grounded ONLY in the provided snippets. Stop tracing when control flow finishes or when no further legitimate hop exists (do NOT invent fake steps).
Return ONLY a JSON object:
{{
    "query": "{flow_query}",
    "relevant": true,
    "summary": "High-level overview of the execution flow across the system",
    "total_hops": 2,
    "hops": [
        {{
            "hop_number": 1,
            "title": "Entry point / Request Handler",
            "file_path": "path/to/file.ext",
            "line_range": "15-30",
            "function_name": "handleLogin",
            "code_snippet": "const user = await authService.login(req.body);",
            "narration": "Detailed explanation of what happens in this step",
            "next_call": "authService.login in auth.ts"
        }}
    ]
}}
"""
        try:
            creds = credentials or GenerationCredentials(
                gemini_api_key=override_key,
                groq_api_key=override_groq_key,
                provider_preference=provider_preference,
            )
            gen_req = GenerationRequest(
                task_name="code_trace",
                prompt=prompt,
                system_instruction=TRACER_SYSTEM_PROMPT,
                use_high_quality=False,
                json_object_mode=True,
            )
            gen_res = await llm_gateway.generate_text(gen_req, creds)
            response_text = gen_res.text
            cleaned = response_text.strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            parsed = json.loads(cleaned.strip())
            if "relevant" not in parsed:
                parsed["relevant"] = len(parsed.get("hops", [])) > 0
            parsed["generation"] = {
                "provider": gen_res.provider,
                "model": gen_res.model,
                "fallback_used": gen_res.fallback_used,
            }
            return parsed
        except (GeminiServiceError, LLMServiceError):
            raise
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise LLMServiceError("LLM returned an invalid code trace. No unverified trace was generated.") from exc

    async def stream_hop_narration(
        self,
        repo_id: str,
        flow_query: str,
        hop_info: Dict[str, Any],
        override_key: Optional[str] = None,
        override_groq_key: Optional[str] = None,
        provider_preference: Optional[str] = None,
        credentials: Optional[GenerationCredentials] = None,
    ) -> AsyncGenerator[str, None]:
        prompt = f"""
Flow Query: "{flow_query}"
Current Hop Step: {hop_info.get('title')} ({hop_info.get('file_path')})
Function: {hop_info.get('function_name')}
Snippet:
```
{hop_info.get('code_snippet')}
```

Provide an engaging, live audio-style step narration explaining exactly how variables mutate, control passes to the next layer, and potential failure modes to watch out for.
"""
        creds = credentials or GenerationCredentials(
            gemini_api_key=override_key,
            groq_api_key=override_groq_key,
            provider_preference=provider_preference,
        )
        gen_req = GenerationRequest(
            task_name="hop_narration",
            prompt=prompt,
            system_instruction=TRACER_SYSTEM_PROMPT,
            use_high_quality=False,
        )
        async for frame in llm_gateway.generate_stream(gen_req, creds):
            if "text" in frame:
                yield frame["text"]

tracer_service = TracerService()
