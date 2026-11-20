"""Decide whether a prompt needs workspace tools, and recover tool calls small models print as text.

Why this exists (measured with llama3.2 3B on Ollama): when any tools are attached, the llama3.2
chat template appends every tool schema (~1.3k tokens for FRIDAY's 24 tools) to the newest user
message and instructs the model to "respond with a JSON for a function call". Every question then
re-evaluates ~1.4k prompt tokens before the first word, and plain questions ("what is 2 + 2?")
come back as a JSON blob such as {"name": "add", ...} instead of an answer.

Routing sends the tool schemas only when the prompt is about the workspace or asks for an action.
If the model still asks for a tool in a chat-only step, the engine escalates that step to tools,
so a mis-route costs one extra model call rather than a wrong answer.
"""
from __future__ import annotations

import json
import re
from typing import Any

ROUTING_MODES = ("auto", "always", "never")
# Tool sets sent to the model. "full" is every tool; nothing is ever removed from the agent.
PROFILES = ("none", "read", "full")
# Workspace questions get this read-only core (~510 prompt tokens instead of ~1,680 for all 24).
READ_TOOLS = ("read_file", "list_dir", "glob", "grep", "git_status", "git_diff", "git_log", "git_show")

# A file name (README.md, llm.py) or a path (./x, ~/y, src/app, friday_agent/llm.py).
# "and/or" or "input/output" are not paths: a bare a/b needs an extension or a leading ./ ~/ /.
_EXT = (r"(?:py|pyi|ts|tsx|js|jsx|mjs|cjs|json|md|css|scss|html|toml|ya?ml|txt|cfg|ini|cpp|cc|h|hpp|"
        r"rs|go|java|kt|rb|php|sh|ps1|sql|lock|csv|xml|ipynb)")
_PATH = re.compile(
    rf"(?:(?<![\w/])(?:\./|\.\./|~/|/)[\w.-]+)"
    rf"|(?:\b[\w.-]+(?:/[\w.-]+)+\.{_EXT}\b)"
    rf"|(?:\b[\w-]+\.{_EXT}\b)",
    re.IGNORECASE,
)
# Nouns that point at the user's own project or machine.
_WORKSPACE = re.compile(
    r"\b(?:files?|folders?|director(?:y|ies)|repo(?:sitory)?|project|codebase|workspace|"
    r"source tree|(?:this|the|my|our|your) (?:code|app|application|backend|frontend|module|package|"
    r"function|class|script|tests?|config|setup)|working tree|readme|changelog)\b",
    re.IGNORECASE,
)
# Version control and tooling words that only make sense against this machine.
_ACTIONS = re.compile(
    r"\b(?:git|commits?|branch(?:es)?|stash|rebase|pull request|PR ?#?\d+|worktrees?|"
    r"pytest|npm|pnpm|yarn|pip install|docker|cargo|working diff|"
    r"(?:the|my|our) tests?|test suite|code[- ]review|security[- ]review)\b",
    re.IGNORECASE,
)
# An imperative that acts on this machine, at the start of the request.
_IMPERATIVE = re.compile(
    r"^\s*(?:(?:please|can you|could you|would you|now|go ahead and)[\s,]+)*"
    r"(?:run|execute|install|build|compile|lint|deploy|commit|push|revert|open|edit|modify|"
    r"refactor|rename|delete|inspect|grep|debug|fix|undo)\b",
    re.IGNORECASE,
)
# Informational openings: "how do I run a script?" is a question, not a request to run one.
_INFO_QUESTION = re.compile(
    r"^\s*(?:how (?:do|does|can|should|would|to)|what(?:'s| is| are| does)|why|when|who|which|"
    r"explain|define|describe|tell me about|difference between|compare|is it|are there|can you explain)\b",
    re.IGNORECASE,
)
# A follow-up that refers back to something found with tools.
_ANAPHORA = re.compile(r"\b(?:it|its|that|this|those|these|there|same|above|the file|the function)\b",
                       re.IGNORECASE)
_CODE_BLOCK = re.compile(r"```")
# Requests that change something need the full tool set.
_WRITE_INTENT = re.compile(
    r"\b(?:edit|change|modify|fix|refactor|rename|delete|remove|create|add|write|update|implement|"
    r"replace|insert|move|commit|push|install|run|execute|build|deploy|revert|undo|patch|merge|"
    r"stage|checkout|branch off|open a pr|make (?:a|the) (?:change|file|commit))\b",
    re.IGNORECASE,
)
_LOOKUP = re.compile(
    r"^\s*(?:(?:please|can you|could you)[\s,]+)*(?:show|list|find|search|where|review|summari[sz]e|"
    r"describe|explain|read|look|tell me|check)\b",
    re.IGNORECASE,
)


def tool_profile(prompt: str, *, previous_used_tools: bool = False) -> tuple[str, str]:
    """Return ("none" | "read" | "full", reason) for one user prompt."""
    use, reason = wants_tools(prompt, previous_used_tools=previous_used_tools)
    if not use:
        return "none", reason
    text = prompt.strip()
    asks = "?" in text or bool(_INFO_QUESTION.search(text)) or bool(_LOOKUP.search(text))
    if asks and not _WRITE_INTENT.search(text):
        return "read", reason + " (look-up)"
    return "full", reason


def wants_tools(prompt: str, *, previous_used_tools: bool = False) -> tuple[bool, str]:
    """Return (use_tools, reason) for one user prompt."""
    text = prompt.strip()
    has_code = bool(_CODE_BLOCK.search(text))
    if _PATH.search(text) and not has_code:
        return True, "mentions a file or path"
    if _WORKSPACE.search(text):
        return True, "refers to the workspace"
    if not _INFO_QUESTION.search(text):
        if _ACTIONS.search(text):
            return True, "mentions version control or project tooling"
        if _IMPERATIVE.search(text) and not has_code:
            return True, "asks for an action on this machine"
    if previous_used_tools and len(text) < 240 and _ANAPHORA.search(text):
        return True, "follow-up to a tool turn"
    return False, "general question"


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def looks_like_tool_json(text: str) -> bool:
    """True while streamed text could still turn out to be a JSON tool call (hold it back)."""
    head = text.lstrip()
    if not head:
        return True
    if head.startswith("{") or head.startswith("<|python_tag|>"):
        return True
    fence = "```json"
    probe = head[: len(fence)].lower()
    return fence.startswith(probe) or probe.startswith(fence)


_CALL_START = re.compile(
    r'^\s*(?:<\|python_tag\|>\s*)?(?:```(?:json)?\s*)?\{\s*"(?:name|function|type"\s*:\s*"function)"\s*[:,]',
    re.IGNORECASE)


def looks_like_broken_call(text: str) -> bool:
    """A reply that starts like a JSON function call (e.g. '{"name": "glob", "parameters{...') but is not valid."""
    return bool(_CALL_START.match(text)) and parse_text_tool_call(text) is None


def parse_text_tool_call(text: str) -> dict[str, Any] | None:
    """Parse a reply that is only a JSON function call: {"name": ..., "parameters"|"arguments": {...}}.

    Returns {"name": str, "arguments": dict} or None when the text is not such a call.
    """
    body = text.strip()
    if body.startswith("<|python_tag|>"):
        body = body[len("<|python_tag|>"):].strip()
    body = _FENCE.sub("", body).strip()
    if not body.startswith("{"):
        return None
    try:
        value, end = json.JSONDecoder().raw_decode(body)
    except json.JSONDecodeError:
        return None
    if body[end:].strip().strip(";").strip():
        return None  # prose after the object: it is an answer that contains JSON, not a call
    if isinstance(value, dict) and isinstance(value.get("function"), dict):
        value = value["function"]  # OpenAI style: {"type": "function", "function": {...}}
    if not isinstance(value, dict) or not isinstance(value.get("name"), str):
        return None
    if not ({"parameters", "arguments"} & set(value)) and len(value) > 1:
        return None
    arguments = value.get("parameters", value.get("arguments", {}))
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            return None
    if not isinstance(arguments, dict):
        return None
    if arguments.get("type") == "object" and isinstance(arguments.get("properties"), dict):
        return None  # the model echoed a tool *schema*, not a call
    return {"name": value["name"], "arguments": arguments}


def split_trailing_call(text: str) -> tuple[str, dict[str, Any] | None]:
    """Split "some prose ... {"name": "grep", "parameters": {...}}" into (prose, call).

    Small models sometimes narrate and then print the call at the end of the reply.
    Returns (text, None) when the reply does not end with a JSON function call.
    """
    body = text.rstrip()
    if body.endswith("```"):
        body = body[:-3].rstrip()
    if not body.endswith("}"):
        return text, None
    start = body.rfind('{"name"')
    if start < 0:
        start = body.rfind("{\n")
    while start > 0:
        call = parse_text_tool_call(body[start:])
        if call is not None:
            prose = re.sub(r"```(?:json)?\s*$", "", body[:start].rstrip()).rstrip()
            return prose, call
        start = body.rfind("{", 0, start)
    return text, None
