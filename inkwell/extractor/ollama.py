"""The one place that talks to Ollama."""
import json
import sys
import urllib.request

from .config import (
    EXTRACTION_MODEL,
    MAX_NUM_CTX,
    NARRATIVE_MODEL,
    NUM_CTX,
    OLLAMA_HOST,
    REQUEST_TIMEOUT,
)


def estimate_tokens(*texts) -> int:
    """Rough token count — ~3.5 characters per token for English prose."""
    return int(sum(len(t) for t in texts) / 3.5)


def context_size(prompt_tokens: int, max_tokens: int) -> int:
    """The num_ctx a call needs: the shared default unless the prompt outgrows it.

    Ollama drops the front of a prompt that exceeds num_ctx without saying so,
    which takes the system prompt and its grounding rules with it. Growing the
    window for the rare oversized call costs one model reload; truncating costs
    a recap written without its rules.
    """
    needed = prompt_tokens + max_tokens + 1024
    if needed <= NUM_CTX:
        return NUM_CTX
    size = NUM_CTX
    while size < needed and size < MAX_NUM_CTX:
        size *= 2
    if needed > size:
        print(
            f"Warning: prompt (~{prompt_tokens} tokens) exceeds the {size}-token context "
            "limit; Ollama will truncate its beginning.",
            file=sys.stderr,
        )
    return size


def ollama_generate(system_prompt, user_message, model=NARRATIVE_MODEL, temperature=0.7, max_tokens=4096, json_schema=None):
    """Call Ollama's chat API and return the reply text.

    `json_schema` constrains the reply to that JSON Schema (Ollama structured
    outputs), which is stricter than bare JSON mode — the keys and types come
    back as asked instead of whatever shape the model improvises.
    """
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ],
        "stream": False,
        # Every pass here is summarizing or extracting, not reasoning. A thinking
        # trace would spend minutes per chunk on tokens nobody reads, and eat into
        # num_predict before the answer starts.
        "think": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
            "num_ctx": context_size(estimate_tokens(system_prompt, user_message), max_tokens),
        }
    }
    if json_schema is not None:
        payload["format"] = json_schema
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA_HOST}/api/chat",
        data=data,
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as response:
        result = json.loads(response.read().decode("utf-8"))
        return result["message"]["content"].strip()


def missing_models(models=(NARRATIVE_MODEL, EXTRACTION_MODEL)) -> list:
    """Return the models Ollama does not have; raise if Ollama is unreachable.

    Checked before transcription starts, so a stopped Ollama or an unpulled
    model is reported in seconds instead of after hours of Whisper.
    """
    with urllib.request.urlopen(f"{OLLAMA_HOST}/api/tags", timeout=10) as response:
        tags = json.loads(response.read().decode("utf-8"))
    installed = set()
    for m in tags.get("models", []):
        name = m.get("name", "")
        installed.add(name)
        if name.endswith(":latest"):
            installed.add(name[: -len(":latest")])
    return sorted({m for m in models if m not in installed})
