"""Build the synthetic VCR cassettes for M2-T12 (no API calls).

Some integration scenarios cannot be produced by a real OpenRouter call:
  - malformed primary → freetext fallback (test_openai_fallback_freetext, #5)
  - both attempts malformed → LLMUnrecoverableError (#6)
  - HTTP 429 → LLMRateLimitError (#10)
  - primary + fallback cost aggregation, n_attempts=2 (#12)
  - Anthropic via OpenRouter: claude returns HTTP 400 for response_format=json_schema
    (it needs tool-use), so #2/#8/#14 are synthesized with OpenAI-style valid
    responses (the format OpenRouter normalizes to). Native Anthropic format is
    covered by unit tests; live Anthropic is verified direct at M6 (ADR-0008).

All request bodies are DERIVED from the real gpt-4o structured cassette
(test_openai_invoke_structured.yaml). vcrpy matches request bodies via a
JSON-aware transformer (json.loads both sides → order-independent), so a derived
body matches the live request as long as the parsed dict is equal. Responses are
the real valid gpt-4o response, doctored per scenario.

Run: uv run python scripts/build_synthetic_cassettes.py
"""

import copy
import importlib.util
import json
from pathlib import Path

import yaml

from aiat.llm.structured import FALLBACK_SUFFIX

CASS = Path("tests/cassettes")
_BASE = CASS / "test_openai_invoke_structured.yaml"

# Import the exact prompt the tests use, without making tests/ a package.
_spec = importlib.util.spec_from_file_location(
    "_tllm", Path("tests/integration/test_llm_providers.py")
)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
PROMPT: str = _mod._DECISION_PROMPT

# Valid-JSON-but-invalid-TradeDecision → triggers the parsing-error path (both the
# structured primary and the freetext fallback classify this as a parse failure).
MALFORMED = json.dumps({"note": "intentionally invalid TradeDecision for VCR replay"})


def _load_base() -> tuple[dict, dict]:
    data = yaml.safe_load(_BASE.read_text())
    inter = data["interactions"][0]
    return copy.deepcopy(inter["request"]), copy.deepcopy(inter["response"])


def _strip_body_headers(resp: dict) -> None:
    headers = resp.get("headers", {})
    for key in list(headers):
        if key.lower() in ("content-length", "transfer-encoding", "content-encoding"):
            del headers[key]


def _req(base_req: dict, mutate) -> dict:  # type: ignore[no-untyped-def]
    r = copy.deepcopy(base_req)
    body = json.loads(r["body"])
    mutate(body)
    r["body"] = json.dumps(body)
    return r


def main() -> None:
    base_req, base_resp = _load_base()

    # --- request variants (derived from the real gpt-4o structured request) ---
    def claude_req(temp: float) -> dict:
        def m(b: dict) -> None:
            b["model"] = "anthropic/claude-sonnet-4.5"
            b["temperature"] = temp

        return _req(base_req, m)

    def freetext_req() -> dict:
        def m(b: dict) -> None:
            b.pop("response_format", None)
            b["messages"][0]["content"] = PROMPT + FALLBACK_SUFFIX

        return _req(base_req, m)

    struct_req = copy.deepcopy(base_req)

    # --- response variants (derived from the real valid gpt-4o response) ---
    def valid_resp(model: str | None = None) -> dict:
        r = copy.deepcopy(base_resp)
        if model:
            body = json.loads(r["body"]["string"])
            body["model"] = model
            r["body"]["string"] = json.dumps(body)
        _strip_body_headers(r)
        return r

    def malformed_resp() -> dict:
        r = copy.deepcopy(base_resp)
        body = json.loads(r["body"]["string"])
        body["choices"][0]["message"]["content"] = MALFORMED
        r["body"]["string"] = json.dumps(body)
        _strip_body_headers(r)
        return r

    def rate_limit_resp() -> dict:
        r = copy.deepcopy(base_resp)
        r["status"] = {"code": 429, "message": "Too Many Requests"}
        r["body"]["string"] = json.dumps(
            {"error": {"message": "Rate limit exceeded", "type": "rate_limit_error", "code": 429}}
        )
        _strip_body_headers(r)
        return r

    def interaction(req: dict, resp: dict) -> dict:
        return {"request": req, "response": resp}

    def write(name: str, interactions: list[dict]) -> None:
        doc = {"version": 1, "interactions": interactions}
        path = CASS / f"{name}.yaml"
        with path.open("w") as fh:
            yaml.safe_dump(doc, fh, allow_unicode=True, sort_keys=False, width=10**6)
        print(f"wrote {path} ({len(interactions)} interaction(s))")  # noqa: T201

    cl07 = claude_req(0.7)
    cl10 = claude_req(1.0)
    ft = freetext_req()

    # #2 / #8 — anthropic structured + cost (synthetic valid)
    write(
        "test_anthropic_invoke_structured",
        [interaction(cl07, valid_resp("anthropic/claude-sonnet-4.5"))],
    )
    write(
        "test_cost_tracking_anthropic",
        [interaction(cl07, valid_resp("anthropic/claude-sonnet-4.5"))],
    )
    # #14 — anthropic thinking (temp 1.0); reasoning_tokens>=0 assertion is lenient
    write(
        "test_anthropic_thinking_usage",
        [interaction(cl10, valid_resp("anthropic/claude-sonnet-4.5"))],
    )

    # #5 / #12 — malformed primary then valid freetext fallback
    fallback_pair = [
        interaction(copy.deepcopy(struct_req), malformed_resp()),
        interaction(copy.deepcopy(ft), valid_resp()),
    ]
    write("test_openai_fallback_freetext", copy.deepcopy(fallback_pair))
    write("test_cost_aggregation_primary_plus_fallback", copy.deepcopy(fallback_pair))

    # #6 — both attempts malformed → unrecoverable
    write(
        "test_llm_unrecoverable_error",
        [
            interaction(copy.deepcopy(struct_req), malformed_resp()),
            interaction(copy.deepcopy(ft), malformed_resp()),
        ],
    )

    # #10 — HTTP 429 (allow_playback_repeats on the test handles SDK retries)
    write(
        "test_rate_limit_propagation", [interaction(copy.deepcopy(struct_req), rate_limit_resp())]
    )

    # #7 — cost tracking openai: identical request to #1, reuse the real response
    write("test_cost_tracking_openai", [interaction(copy.deepcopy(struct_req), valid_resp())])


if __name__ == "__main__":
    main()
