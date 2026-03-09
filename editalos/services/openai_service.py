from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from openai import OpenAI

from editalos.config import get_settings


settings = get_settings()


class OpenAIService:
    """Thin wrapper around the OpenAI SDK.

    Design notes:
    - Uses Responses API for new text workloads.
    - Keeps static instructions at the beginning to improve prompt caching opportunities.
    - Supports `service_tier="flex"` for low-priority tasks.
    - Exposes JSONL generation and submission helpers for Batch API.
    """

    def __init__(self) -> None:
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY não encontrado no .env")
        self.client = OpenAI(api_key=settings.openai_api_key)

    def _base_response_kwargs(self, *, cache_suffix: str, use_flex: bool = False) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": settings.openai_text_model,
            "prompt_cache_key": f"{settings.openai_prompt_cache_key_prefix}:{cache_suffix}",
            "prompt_cache_retention": settings.openai_prompt_cache_retention,
        }
        if use_flex or settings.openai_flex_enabled:
            kwargs["service_tier"] = "flex"
        elif settings.openai_service_tier and settings.openai_service_tier != "default":
            kwargs["service_tier"] = settings.openai_service_tier
        return kwargs

    def text(self, *, instructions: str, input_text: str, cache_suffix: str, use_flex: bool = False) -> str:
        response = self.client.responses.create(
            instructions=instructions,
            input=input_text,
            **self._base_response_kwargs(cache_suffix=cache_suffix, use_flex=use_flex),
        )
        return response.output_text

    def structured(
        self,
        *,
        instructions: str,
        input_text: str,
        schema_name: str,
        schema: dict[str, Any],
        cache_suffix: str,
        use_flex: bool = False,
    ) -> dict[str, Any]:
        response = self.client.responses.create(
            instructions=instructions,
            input=input_text,
            text={
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "schema": schema,
                    "strict": True,
                }
            },
            **self._base_response_kwargs(cache_suffix=cache_suffix, use_flex=use_flex),
        )
        return json.loads(response.output_text)

    def embeddings(self, texts: list[str]) -> list[list[float]]:
        result = self.client.embeddings.create(
            model=settings.openai_embedding_model,
            input=texts,
        )
        return [item.embedding for item in result.data]

    def write_batch_jsonl(self, requests: Iterable[dict[str, Any]], output_path: str | Path) -> Path:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8") as fh:
            for request in requests:
                fh.write(json.dumps(request, ensure_ascii=False) + "\n")
        return output

    def submit_batch(self, jsonl_path: str | Path, endpoint: str = "/v1/responses") -> Any:
        with Path(jsonl_path).open("rb") as fh:
            file_obj = self.client.files.create(file=fh, purpose="batch")
        batch = self.client.batches.create(
            input_file_id=file_obj.id,
            endpoint=endpoint,
            completion_window="24h",
        )
        return batch

    def build_response_batch_request(
        self,
        *,
        custom_id: str,
        instructions: str,
        input_text: str,
        cache_suffix: str,
        use_flex: bool = False,
    ) -> dict[str, Any]:
        body = {
            "instructions": instructions,
            "input": input_text,
            **self._base_response_kwargs(cache_suffix=cache_suffix, use_flex=use_flex),
        }
        return {
            "custom_id": custom_id,
            "method": "POST",
            "url": "/v1/responses",
            "body": body,
        }

    def build_embedding_batch_request(self, *, custom_id: str, texts: list[str]) -> dict[str, Any]:
        return {
            "custom_id": custom_id,
            "method": "POST",
            "url": "/v1/embeddings",
            "body": {
                "model": settings.openai_embedding_model,
                "input": texts,
            },
        }
