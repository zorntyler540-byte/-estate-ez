"""Convert Pydantic models to JSON schemas accepted by Gemini structured output."""

from __future__ import annotations

import copy
from typing import Any

from pydantic import BaseModel


def pydantic_model_to_gemini_schema(model: type[BaseModel]) -> dict[str, Any]:
    """
    Gemini rejects Pydantic ``model_json_schema()`` payloads that include
    ``additionalProperties`` (from ``ConfigDict(extra="forbid")``).
    """
    schema = copy.deepcopy(model.model_json_schema())
    _strip_unsupported_gemini_schema_keys(schema)
    return schema


def _strip_unsupported_gemini_schema_keys(obj: Any) -> None:
    if isinstance(obj, dict):
        obj.pop("additionalProperties", None)
        # Pydantic uses "title" for schema metadata (human label). Do not pop when the
        # value is a property sub-schema — e.g. properties["title"] for a field named title.
        if isinstance(obj.get("title"), str):
            obj.pop("title", None)
        for value in obj.values():
            _strip_unsupported_gemini_schema_keys(value)
    elif isinstance(obj, list):
        for item in obj:
            _strip_unsupported_gemini_schema_keys(item)
