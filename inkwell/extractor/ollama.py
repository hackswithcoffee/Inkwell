"""The one place that talks to Ollama."""
import json
import sys
import urllib.error
import urllib.request

from .config import EXTRACTION_MODEL, NARRATIVE_MODEL, OLLAMA_HOST

def ollama_generate(system_prompt, user_message, model=NARRATIVE_MODEL, temperature=0.7, max_tokens=4096, json_mode=False, num_ctx=8192):
    """Call Ollama REST API."""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ],
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
            "num_ctx": num_ctx
        }
    }
    if json_mode:
        payload["format"] = "json"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA_HOST}/api/chat",
        data=data,
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=600) as response:
        result = json.loads(response.read().decode("utf-8"))
        return result["message"]["content"].strip()
