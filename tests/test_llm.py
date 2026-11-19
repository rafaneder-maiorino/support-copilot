"""Real-provider wiring without a key: a spy chat model stands in for Gemini/OpenAI.

The fake provider covers the /chat path end to end, but not ChatModelGenerator, the adapter that a
real LLM goes through. These tests pin what that adapter sends (system prompt + context as data)
and how it reads replies, so the first run with a real key only exercises the network.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from support_copilot.rag.chat import ChatService
from support_copilot.rag.llm import ChatModelGenerator
from support_copilot.rag.prompts import SYSTEM_PROMPT
from support_copilot.rag.retriever import Hit

HIT = Hit(
    text="Go to System > Access > Users. Click Add. Fill in the form.",
    page=7,
    section="manual/users",
    score=0.8,
    chunk_id=1,
)


class SpyChatModel:
    """Duck-typed stand-in for BaseChatModel.invoke: records the messages, returns a fixed reply."""

    def __init__(self, content: str | list) -> None:
        self.content = content
        self.calls: list[list[BaseMessage]] = []

    def invoke(self, messages: list[BaseMessage]) -> AIMessage:
        self.calls.append(messages)
        return AIMessage(content=self.content)


class StubRetriever:
    def __init__(self, hits: list[Hit]) -> None:
        self.hits = hits

    def relevant(self, question: str) -> list[Hit]:
        return self.hits


def test_adapter_sends_system_prompt_then_context_and_question():
    spy = SpyChatModel("Use System > Access > Users [p. 7]")
    ChatModelGenerator("openai", "gpt-test", spy).generate("how do I add a user?", [HIT])  # type: ignore[arg-type]

    (messages,) = spy.calls
    assert [type(m) for m in messages] == [SystemMessage, HumanMessage]
    assert messages[0].content == SYSTEM_PROMPT
    human = messages[1].content
    assert human.startswith("CONTEXT:\n[p. 7 | manual/users]\nGo to System > Access > Users.")
    assert human.endswith("QUESTION: how do I add a user?")


def test_adapter_strips_plain_text_replies():
    spy = SpyChatModel("  Use System > Access > Users [p. 7]\n")
    answer = ChatModelGenerator("gemini", "gemini-test", spy).generate("q?", [HIT])  # type: ignore[arg-type]
    assert answer == "Use System > Access > Users [p. 7]"


def test_adapter_joins_content_blocks():
    blocks = [{"type": "text", "text": "Use System > Access > Users "}, {"type": "text", "text": "[p. 7]"}]
    spy = SpyChatModel(blocks)
    answer = ChatModelGenerator("gemini", "gemini-test", spy).generate("q?", [HIT])  # type: ignore[arg-type]
    assert answer == "Use System > Access > Users [p. 7]"


def test_real_provider_path_still_goes_through_the_output_guardrail():
    """A reply without a citation from a retrieved page is replaced, whatever produced it."""
    cited = ChatModelGenerator("openai", "gpt-test", SpyChatModel("Click Add [p. 7]"))  # type: ignore[arg-type]
    uncited = ChatModelGenerator("openai", "gpt-test", SpyChatModel("Click Add."))  # type: ignore[arg-type]
    wrong_page = ChatModelGenerator("openai", "gpt-test", SpyChatModel("Click Add [p. 99]"))  # type: ignore[arg-type]

    assert ChatService(StubRetriever([HIT]), cited).ask("add user?").grounded
    for generator in (uncited, wrong_page):
        out = ChatService(StubRetriever([HIT]), generator).ask("add user?")
        assert out.llm_called and not out.grounded
