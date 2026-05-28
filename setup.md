# Setup

How to get a LinkedOut node running from a fresh checkout — two laptops, talking to each other's local AI through an encrypted relay.

## 1. Prerequisites

| Need | Why | How |
|---|---|---|
| **Python ≥ 3.10** | pydantic v2, `asyncio.to_thread` | `brew install python` / system python / pyenv |
| Ollama | runs the local LLM that answers `ask` queries | `brew install ollama` (macOS) or [ollama.com/download](https://ollama.com/download) |
| `pip` deps | `pydantic`, `cryptography` — see `requirements.txt` | step 2 |

Check your Python first — 3.10 or newer:

```bash
python3 --version    # need >= 3.10
```

`ai_client.py` itself has **no pip dependencies** — it talks to Ollama over `urllib`. Only the protocol layer needs pydantic/cryptography.

## 2. Python dependencies (one-shot)

From the repo root:

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Sanity check:

```bash
python3 -c "import pydantic, cryptography; print('ok')"
python3 e2ee.py        # runs the crypto self-test
python3 app_layer.py   # runs the app-layer self-test
```

> The `.venv/` folder is gitignored — each teammate creates their own; it never gets committed. Re-run `source .venv/bin/activate` in every new shell.

## 3. Ollama

Install, start the daemon, pull any one chat model. **The code does not hardcode a model name** — each teammate picks what fits their machine.

```bash
# macOS: starts on login. If not running:
brew services start ollama
# or just:
ollama serve   # foreground

# Pull whatever fits your RAM / disk. Examples:
ollama pull qwen2.5:7b      # ~4.7 GB, runs on most laptops
ollama pull dolphin3        # ~4.9 GB, fastest
ollama pull qwen2.5:14b     # ~9 GB, better quality
ollama pull gemma4:26b      # ~17 GB, best quality if you have the RAM

# Verify
ollama list
curl -s http://localhost:11434/api/tags | head
```

### Model selection (group project: each teammate sets their own)

`ai_client.resolve_model()` picks the model in this order:

1. `--model <name>` passed to `p2p_node.py` (explicit per-run override)
2. `LINKEDOUT_MODEL` environment variable
3. `OLLAMA_MODEL` environment variable
4. The first model returned by `ollama list` (auto-detect)
5. Raises with an actionable message ("run `ollama pull ...`")

Recommended: each teammate sets `LINKEDOUT_MODEL` in their shell rc once and forgets about it:

```bash
# in ~/.zshrc or ~/.bashrc
export LINKEDOUT_MODEL=qwen2.5:7b   # whatever you pulled
```

Set `OLLAMA_HOST=http://...:11434` if your Ollama daemon is on another machine.

Smoke-test the AI client without any networking:

```bash
.venv/bin/python ai_client.py "What is end-to-end encryption?"
```

It prints the model it resolved to (`🤖 Asking local Ollama (<model>)`) followed by the answer.

> Don't push the model file. Models live in `~/.ollama/models/` outside this repo. The repo's `.gitignore` already excludes `*.gguf`, `*.safetensors`, `*.bin`, `models/`, `.ollama/` defensively.

## 4. Create your share folder

Each node serves files from `share/`. **Four zones**, gated by the caller's tier (see §6):

| Zone | Who can see it | Notes |
|---|---|---|
| `share/read-only/`   | everyone (`common`+) | read only |
| `share/read&append/` | everyone (`common`+) | read **and** append |
| `share/task/`        | `task` and `personal` peers | shared task context |
| `share/personal/`    | `personal` peers only | your private vault |

`p2p_node.py` creates all four on first run (matches the proposal's Personal / Task / Common vault diagram). Drop notes/markdown/text files in any zone — they become the AI's `ask` context **only for peers whose tier can see that zone**:

```bash
echo "My favourite books: Snow Crash, The Three-Body Problem." > share/read-only/notes.md
echo "Sprint goal: ship the demo." > share/task/plan.md
echo "(private stuff only personal-tier peers can reach)" > share/personal/secret.md
```

## 5. Generate your identity (first run prints your public key)

```bash
python3 p2p_node.py --port 8001 --name Alice
```

The node prints:

```
🔑 My public key（分享給朋友，加入對方 agent list）:
   <64 hex chars>
```

Copy that string. Stop the node (Ctrl-C). The private key was saved to `linkedout_8001.key` (mode 0600). Don't commit it — `.gitignore` excludes `*.key`.

## 6. Exchange public keys with your peer

Out-of-band — Signal / iMessage / paper / whatever. Each side gives the other their 64-hex pubkey.

Add the other side to your trust list (persists to `agents.json`). Optionally set their **tier** — how deep into your `share/` they can reach:

```bash
python3 agents.py add <peer-pubkey> --name Bob              # defaults to tier=common
python3 agents.py add <peer-pubkey> --name Bob --tier task  # also sees share/task/
python3 agents.py set-tier <peer-pubkey> personal           # change later, sees everything
python3 agents.py list                                       # shows the tier column
```

Tiers (low → high): `common` < `task` < `personal`. A peer only ever sees zones at or below their tier — for `read`, `append`, `list`, **and** what the local AI is allowed to use as `ask` context. A `common` peer asking about your `personal/` files literally never has them in the model's context, so the AI cannot leak them (not just "refuses" — they're absent).

> Changing a peer's tier takes effect when the **receiver** node restarts (the trust list is loaded at startup).

`agents.json` is per-machine and gitignored. Entries with no `tier` field (older files) are treated as `common`.

## 7. Pick a connection mode

### Mode A — Relay (cross-network, recommended)

Pick one machine (or a cheap VPS) to run the relay. Open the port so both peers can reach it.

On the relay host:

```bash
python3 relay_server.py --port 9000
```

Note its public/LAN IP (the script prints it).

### Mode B — Direct LAN (same WiFi)

No relay needed; both nodes talk over LAN sockets. RESPONSE round-trip is relay-only today, so direct mode is fine for one-shot demos but the AI's reply prints on the *receiver's* terminal, not the sender's.

## 8. Run two nodes

### Alice (receiver — keeps her node listening for requests)

```bash
python3 p2p_node.py --port 8001 --name Alice \
  --server-ip <RELAY_IP> --server-port 9000
```

She'll see:

```
🤝 信任白名單（1 人，來自 agents.json）: [Bob(<pubkey>…)]
🌐 [Mode] Relay 模式
✅ [Relay] Registered as <Alice's pubkey>…
```

### Bob (sender — fires one request and waits for response)

#### Ask Alice's local AI a question
```bash
python3 p2p_node.py --port 8002 --name Bob \
  --server-ip <RELAY_IP> --server-port 9000 \
  --peer-pubkey <Alice's pubkey> \
  --op ask --query "What are your favourite books?"
```

#### Read a file from Alice's share/
```bash
python3 p2p_node.py --port 8002 --name Bob \
  --server-ip <RELAY_IP> --server-port 9000 \
  --peer-pubkey <Alice's pubkey> \
  --op read --path "read-only/notes.md"
```

#### List Alice's share/
```bash
python3 p2p_node.py ... --op list
```

#### Append to Alice's read&append zone
```bash
python3 p2p_node.py ... --op append --path "read&append/log.md" --content "hello from Bob\n"
```

## 9. What you should see

On Alice's terminal (the receiver):

```
📥 [Relay] From: <Bob's pubkey>… | Type: REQUEST
🔓 [Decrypted] from <Bob's pubkey>… ✔ 寄件者已驗證
📂 [Request] id=… op=ask "What are your favourite books?…"
🧠 [Ask] '…' ctx_chunks=1
↩️  [Response] id=… ok=True (AI 回應)
📤 [Relay] Sent to <Bob's pubkey>…（🔒 已加密）
```

On Bob's terminal (the sender):

```
📤 [Relay] Sent to <Alice's pubkey>…（🔒 已加密）
📥 [Relay] From: <Alice's pubkey>… | Type: RESPONSE
🔓 [Decrypted] from <Alice's pubkey>… ✔ 寄件者已驗證
🤖 [Reply id=…] AI 回應：
┌────────────────────────────
│ Based on the read-only notes, your favourite books are Snow Crash and The Three-Body Problem.
└────────────────────────────
```

On the relay (for either direction):

```
📦 <sender>… → <recipient>…  | 🔒 payload encrypted (relay 看不懂內容)
```

The relay log proves the property: it forwards bytes and never sees plaintext.

## 10. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ollama unreachable at http://localhost:11434` | daemon not running | `ollama serve` or `brew services start ollama` |
| Reply takes 30s+ on first `ask` | model cold-load into RAM | normal; subsequent calls are fast |
| `⛔ [Reject] 未授權的寄件者` | sender not in receiver's `agents.json` | `python3 agents.py add <their-pubkey>` on the receiver |
| `🚫 [Decrypt failed]` | wrong pubkey in `--peer-pubkey`, or relay tampered metadata | re-check pubkey; AAD binds the route metadata so any tampering trips this |
| `path_denied` / `not_shared` | path tried to escape `share/` or wasn't under a zone | use `read-only/<file>` or `read&append/<file>` |
| `permission_denied` on append | tried to append into `read-only/` | only `read&append/` accepts appends |
| `ai_error: No Ollama model available …` | No model installed locally | `ollama pull <name>` (any chat model), or set `LINKEDOUT_MODEL` |
| `ai_error: ollama unreachable …` | Ollama daemon not running | `ollama serve` / `brew services start ollama` |

## 11. Files you'll see locally (none committed)

| File | Created by | What |
|---|---|---|
| `linkedout_<port>.key` | first run | your X25519 private key (mode 0600) |
| `agents.json` | `agents.py add` | your trust list |
| `share/` + zone subfolders | first run | what your peers can read/append/ask against |
| `~/.ollama/models/…` | `ollama pull` | the model weights, outside this repo |

All four are in `.gitignore` (the first three explicitly, model files defensively). `git ls-files` should show only source files.

## 12. Cleanup

```bash
# Wipe local state for a fresh start (DESTRUCTIVE — loses your identity)
rm -f linkedout_*.key agents.json
rm -rf share/

# Stop nodes/relay with Ctrl-C in their terminals
```
