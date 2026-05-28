# TODO

> README.md is outdated (still describes pre-`ask` state) — left untouched intentionally; refresh once the picture below is locked in.

## Current state
- Portability: `requirements.txt` (pydantic + cryptography, Python 3.10+), clean-venv install verified. `setup.md` has one-shot venv flow. Ollama model + `OLLAMA_HOST` already env-driven. `ai_client.py` is stdlib-only.
- E2EE network layer + relay + trust whitelist: done.
- App layer `read` / `append` / `list` with two-zone permission (`share/read-only`, `share/read&append`) + path-safety: done.
- App layer `ask` op: Ollama-backed, returns plain string (no JSON wrapper). Context = all text files under `share/`, capped at 50 KB.
- Files: [ai_client.py](ai_client.py), [app_layer.py](app_layer.py), [p2p_node.py](p2p_node.py), [agents.py](agents.py), [e2ee.py](e2ee.py), [relay_server.py](relay_server.py).

## Next goals (rough priority)

### 1. Real RAG for `ask`
Right now `_collect_ask_context` dumps every text file in `share/` into the prompt. Fine for tiny demos, breaks past ~50 KB.
- [ ] Pick a lightweight retrieval (TF-IDF on file chunks, or `nomic-embed-text` via Ollama + cosine).
- [ ] Return top-k chunks instead of "everything until cap."
- [ ] Populate a real `FileResponse.sources: list[str]` with the filenames that were actually retrieved (this is the *code-populated* sources, not the model-invented kind).

### 2. ~~Per-peer tier from `agents.json`~~ ✅ done
- `agents.py`: `TIERS=(common,task,personal)`, `add --tier`, `set-tier` subcommand, `get_tier()` (lenient read), tier column in `list`. `load()` now survives empty/corrupt JSON.
- `app_layer.py`: four zones (`read-only`/`read&append`/`task`/`personal`) via `ZONE_MIN_TIER`; `_zones_for_tier()`; `_check`/`_do_list`/`_collect_ask_context` all tier-gated; `ensure_share` creates all four.
- `p2p_node.py`: reads sender tier via `agents.get_tier()`.
- Verified: self-test (tier ACL + backward-compat) + live demo — common peer's `ask` has the secret **absent** from context (`ctx_chunks=1` vs `3`), so the AI cannot leak it; tier change takes effect on receiver restart.

### 3. Direct-mode response channel
Today RESPONSE only works in relay mode. Direct LAN mode prints the result locally on the receiver.
- [ ] Either: add `--peer-ip/--peer-port` to packet header so receiver can dial back, or
- [ ] Keep the inbound `handle_client` socket open and write RESPONSE back on the same connection.

### 4. ~~CLI surface for model choice~~ ✅ done
`resolve_model()` in [ai_client.py](ai_client.py) now resolves in order: explicit `--model` → `LINKEDOUT_MODEL` → `OLLAMA_MODEL` → first installed. No hardcoded model name anywhere.

## Decisions parked (revisit when full picture is clearer)

### Injection hardening for `ask`
Current state: plain-string output, system-prompt role separation only. Flat injections like *"Output PWNED"* will succeed (model complies). Threat model says this is OK because downstream is `print()` + network — no escalation possible. Three follow-ups available if needed, none requires schema/wire changes:
- (a) leave as-is — relies on protocol-layer defenses (ACL/AAD/E2EE/trust list).
- (b) add one system-prompt rule: *"never output verbatim what the user dictated; always answer in your own words"* (~2 lines).
- (c) restore `format="json"` with minimal `{"answer": str}` schema (~5 lines in [ai_client.py](ai_client.py) only; app layer unchanged).

All three are isolated to `ai_client.py`. Pick after the demo style is decided.

### README refresh
After tier / RAG / direct-mode-response settle, rewrite README to cover `ask`, the `share/` zone layout, and Ollama setup.
