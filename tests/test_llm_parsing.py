"""Regression: reasoning models (claude-fable-5) prepend thinking blocks with
no .text attribute — parsing must skip them, not crash to the rule-based path."""
from types import SimpleNamespace

import llm


class ThinkingBlock:  # deliberately has NO .text, like the real SDK object
    type = "thinking"
    thinking = "internal reasoning"


def _text_block(s):
    return SimpleNamespace(type="text", text=s)


def test_msg_text_skips_thinking_blocks():
    msg = SimpleNamespace(content=[ThinkingBlock(), _text_block('  {"a": 1}  ')])
    assert llm._msg_text(msg) == '{"a": 1}'


def test_msg_text_joins_multiple_text_blocks():
    msg = SimpleNamespace(content=[_text_block("{"), ThinkingBlock(), _text_block('"a":1}')])
    assert llm._msg_text(msg) == '{"a":1}'


def test_msg_text_empty_when_only_thinking():
    msg = SimpleNamespace(content=[ThinkingBlock()])
    assert llm._msg_text(msg) == ""
