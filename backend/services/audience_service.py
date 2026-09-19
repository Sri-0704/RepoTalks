import json
import logging
from typing import Any, Dict, Optional

from backend.config import settings
from backend.services.cache_service import cache_service
from backend.services.context_builder import context_builder
from backend.services.llm_gateway import (
    llm_gateway,
    GenerationCredentials,
    GenerationRequest,
    LLMServiceError,
)
from backend.services.vector_store import vector_store

logger = logging.getLogger(__name__)

AUDIENCE_PROMPTS = {
    "recruiter": "Format explanation for a Tech Recruiter: Highlight tech stack, business impact, key achievements, scale, and modern engineering practices. Keep it punchy.",
    "manager": "Format explanation for a Non-Technical Product Manager: Use clear real-world analogies, explain the user problem solved, ROI, performance benefits, and eliminate deep technical jargon.",
    "developer": "Format explanation for a Senior Software Engineer / Peer: Deep dive into architectural design, data structures, state management, async call flow, API contracts, and trade-offs.",
    "professor": "Format explanation for a Computer Science Professor: Focus on algorithmic complexity, system methodology, theoretical foundation, data structures, and engineering rigor.",
}


class AudienceService:
    async def explain_for_audience(
        self,
        repo_id: str,
        audience: str = "developer",  # recruiter, manager, developer, professor
        file_path: Optional[str] = None,
        override_key: Optional[str] = None,
        override_groq_key: Optional[str] = None,
        provider_preference: Optional[str] = None,
        credentials: Optional[GenerationCredentials] = None,
    ) -> Dict[str, Any]:
        creds = credentials or GenerationCredentials(
            gemini_api_key=override_key,
            groq_api_key=override_groq_key,
            provider_preference=provider_preference,
        )

        audience_key = audience.lower().strip()
        pref = llm_gateway.resolve_effective_preference(GenerationRequest(prompt=""), creds)
        policy_sig = f"{pref}:{settings.GROQ_MODEL}:{settings.DEFAULT_FLASH_MODEL}"
        subkey = f"v3:{audience_key}:{file_path or ''}:{policy_sig}"

        cached = cache_service.get_artifact(repo_id, "audience", subkey)
        if cached is not None:
            logger.info("Returning cached audience explanation for repo '%s' (%s)", repo_id, subkey)
            return cached

        system_inst = AUDIENCE_PROMPTS.get(audience_key, AUDIENCE_PROMPTS["developer"])

        search_query = f"overview main components functionality {file_path or ''}"
        rag_results = await vector_store.search(
            repo_id,
            search_query,
            top_k=5,
            override_key=creds.gemini_api_key,
        )
        context_text = context_builder.build_context(rag_results, max_tokens=4000).context_text

        prompt = f"""
Target Audience: {audience_key.upper()}

Codebase Context:
{context_text}

Task: Provide an audience-tailored project breakdown structured with target-specific vocabulary, key takeaways, and elevator pitch.

Return ONLY a JSON object:
{{
    "audience": "{audience_key}",
    "headline": "One-line summary custom tailored for this audience",
    "elevator_pitch": "30-second explanation designed for this persona",
    "key_highlights": [
        "Point 1 matching audience interests",
        "Point 2 matching audience interests",
        "Point 3 matching audience interests"
    ],
    "detailed_explanation": "Comprehensive multi-paragraph explanation tailored specifically in depth, tone, and vocabulary.",
    "talking_points": [
        "What to say if asked about architecture",
        "What to say if asked about key challenges"
    ]
}}
"""
        gen_res = await llm_gateway.generate_text(
            GenerationRequest(
                task_name=f"audience_{audience_key}",
                prompt=prompt,
                system_instruction=system_inst,
                use_high_quality=False,
                json_object_mode=True,
            ),
            creds,
        )

        try:
            cleaned = gen_res.text.strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            if cleaned.startswith("```"):
                cleaned = cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            parsed = json.loads(cleaned.strip())
            parsed["generation"] = {
                "provider": gen_res.provider,
                "model": gen_res.model,
                "fallback_used": gen_res.fallback_used,
            }
            cache_service.set_artifact(repo_id, "audience", subkey, parsed)
            return parsed
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise LLMServiceError("Model returned an invalid audience explanation. Please try again.") from exc


audience_service = AudienceService()
