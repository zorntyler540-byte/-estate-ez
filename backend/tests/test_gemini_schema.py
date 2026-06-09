"""Tests for Gemini JSON schema conversion."""

from __future__ import annotations

from pipeline.gemini_schema import pydantic_model_to_gemini_schema
from pipeline.schemas import AppraisalCheckResult, PhotoTriageResult


def test_gemini_schema_strips_additional_properties() -> None:
    schema = pydantic_model_to_gemini_schema(PhotoTriageResult)
    blob = str(schema)
    assert "additionalProperties" not in blob
    assert schema["type"] == "object"
    assert "filename" in schema["properties"]


def test_appraisal_check_result_gemini_schema() -> None:
    schema = pydantic_model_to_gemini_schema(AppraisalCheckResult)
    assert "additionalProperties" not in str(schema)
    assert "listing_channel" in schema["properties"]
    assert "recommended_starting_price" in schema["properties"]
    item_schema = schema["$defs"]["AppraisalItemLine"]
    assert "title" in item_schema["properties"]
    assert "title" in item_schema["required"]
