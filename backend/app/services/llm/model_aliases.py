"""Model-name aliases for Together serverless compatibility."""
from __future__ import annotations


SERVERLESS_MODEL_ALIASES = {
    # These model strings require dedicated Together endpoints. Keep accepting
    # them from stale Render env vars / old A-B payloads, but route them to
    # current serverless equivalents so the standard instance keeps working.
    "Qwen/Qwen2.5-72B-Instruct-Turbo": "Qwen/Qwen3-235B-A22B-Instruct-2507-tput",
    "meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo": (
        "meta-llama/Llama-3.3-70B-Instruct-Turbo"
    ),
    # "Qwen/Qwen3.5-9B" is NOT a real Together model (Qwen versions are
    # 1.5 / 2 / 2.5 / 3 — there is no 3.5). The old alias here pointed to it
    # and silently broke every routed call. Send the legacy Mistral name to
    # the same serverless Qwen we already use elsewhere.
    "mistralai/Mistral-Small-24B-Instruct-2501": "Qwen/Qwen3-235B-A22B-Instruct-2507-tput",
    "Qwen/Qwen3.5-9B": "Qwen/Qwen3-235B-A22B-Instruct-2507-tput",
}


def normalize_model_name(model_name: str) -> str:
    """Return a serverless-safe model name when a stale alias is configured."""
    return SERVERLESS_MODEL_ALIASES.get(model_name, model_name)
