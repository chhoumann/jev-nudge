import json
import re
from pathlib import Path

MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_STATE_BYTES = 24000


def clip(text: str, size: int) -> str:
    data = text.encode()
    if len(data) <= size:
        return text
    half = (size - 30) // 2
    return (
        data[:half].decode(errors="ignore")
        + "\n[...truncated...]\n"
        + data[-half:].decode(errors="ignore")
    )


def redact(text: str, api_key: str = "") -> str:
    if api_key:
        text = text.replace(api_key, "[REDACTED]")
    text = re.sub(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]+", r"\1[REDACTED]", text)
    return re.sub(
        r"(?i)((?:api[_-]?key|token|password|secret)\s*[=:]\s*[\"']?)[^\s\"',}]+",
        r"\1[REDACTED]",
        text,
    )


def messages(path: Path) -> list[dict]:
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("transcript exceeds 32 MiB; skipping instead of losing early requests")
    result = []
    with path.open() as file:
        for line in file:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue  # The live writer can leave a partial final line.
            p = row.get("payload", {})
            if row.get("type") == "compacted":
                if p.get("message"):
                    result.append({"role": "summary", "text": p["message"]})
                continue
            if row.get("type") != "response_item":
                continue
            kind = p.get("type")
            role = p.get("role")
            if kind == "message" and role in ("user", "assistant"):
                if p.get("phase") == "analysis":
                    continue
                content = p.get("content", [])
                metadata = p.get("internal_chat_message_metadata_passthrough") or {}
                kinds = metadata.get("content_item_kinds") or []
                parts = []
                for i, item in enumerate(content):
                    if role == "user" and kinds and i < len(kinds):
                        if kinds[i] not in ("user.text", "unknown"):
                            continue
                    text = item.get("text", "")
                    if text.startswith(
                        (
                            "# AGENTS.md instructions",
                            "<environment_context>",
                            "<recommended_plugins>",
                        )
                    ):
                        continue
                    if text:
                        parts.append(text)
                if parts:
                    result.append({"role": role, "text": "\n".join(parts)})
            elif kind in ("function_call", "custom_tool_call"):
                result.append(
                    {
                        "role": "tool_call",
                        "text": clip(
                            str(p.get("name", ""))
                            + " "
                            + str(p.get("arguments", p.get("input", ""))),
                            1200,
                        ),
                    }
                )
            elif kind in ("function_call_output", "custom_tool_call_output"):
                result.append({"role": "tool_result", "text": clip(str(p.get("output", "")), 2000)})
    return result


def build_state(path: Path, last_message: str, api_key: str = "") -> dict:
    history = messages(path)
    if not any(m["role"] == "user" for m in history):
        raise ValueError("no user request in transcript")
    state = {
        "earlier_user_requests": [],
        "recent_messages": [],
        "last_assistant_message": clip(redact(last_message, api_key), 5000),
        "context_truncated": False,
    }
    requests = [m for m in history if m["role"] in ("user", "summary")]
    # Keep the first request as well as recent steering when the history is large.
    selected = requests if len(requests) <= 8 else [requests[0], *requests[-7:]]
    state["context_truncated"] = len(selected) != len(requests)
    for m in selected:
        text = redact(m["text"], api_key)
        short = clip(text, 900)
        state["earlier_user_requests"].append({"role": m["role"], "text": short})
        state["context_truncated"] |= text != short
    for m in reversed(history):
        text = redact(m["text"], api_key)
        short = clip(text, 4000 if m["role"] == "user" else 2000)
        item = {"role": m["role"], "text": short}
        state["recent_messages"].insert(0, item)
        if len(json.dumps(state, ensure_ascii=False).encode()) > MAX_STATE_BYTES:
            state["recent_messages"].pop(0)
            state["context_truncated"] = True
            break
        state["context_truncated"] |= text != short
    return state
