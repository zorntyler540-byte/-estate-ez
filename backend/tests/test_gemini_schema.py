"""Tests for Gemini JSON schema conversion."""

from __future__ import annotations

from pipeline.gemini_schema import pydantic_model_to_gemini_schema
from pipeline.schemas import PhotoTriageResult


def test_gemini_schema_strips_additional_properties() -> None:
    schema = pydantic_model_to_gemini_schema(PhotoTriageResult)
    blob = str(schema)
    assert "additionalProperties" not in blob
    assert schema["type"] == "object"
    assert "filename" in schema["properties"]
