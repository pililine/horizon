"""Tests for shared AI response utilities."""

from src.ai.utils import parse_json_response, strip_thinking_content


def test_strip_thinking_content_removes_xml_think_block():
    text = "<think>I should reason privately.</think>\n{\"score\": 8}"

    assert strip_thinking_content(text) == "{\"score\": 8}"


def test_strip_thinking_content_removes_ollama_thinking_block():
    text = "Thinking...\nprivate reasoning\n...done thinking.\n{\"score\": 8}"

    assert strip_thinking_content(text) == "{\"score\": 8}"


def test_parse_json_response_uses_cleaned_content():
    text = "<think>hidden</think>\n```json\n{\"score\": 8}\n```"

    assert parse_json_response(text) == {"score": 8}
