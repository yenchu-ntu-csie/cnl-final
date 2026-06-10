# Share Audit UI

Use this local dashboard on each person's own computer before starting a node, changing a peer's tier, importing files, or demoing tiered disclosure.

```bash
python3 share_audit_server.py --share share --agents-file agents.json
```

Open the printed `http://127.0.0.1:...` URL. The server is local by default and the UI/API return paths and counts only, never file contents. It is meant to run beside Bob's own `share/` and `agents.json`, not as a central audit service.

## Ask Composer

Use `Ask Composer` as Bob's starting point. Type or select a concrete question, choose `remote answer` or `local synthesis`, then click `Plan ask`.

The plan shows the target peer/tier, visible context zones, excluded zones, visible-entry count, ask chunk count, readiness status, and a `p2p_node.py --op ask --query ...` command preview. This is a preflight step only: it does not call Ollama, contact the relay, or return raw chunks/file bodies to the browser.

Use this before sending the real question through `p2p_node.py`, especially when the question mentions `task/` or `personal/`. If a question mentions a zone the selected peer/tier cannot see, the composer marks the ask as constrained instead of pretending the answer will include that context.

## Bob Scenario Walkthrough

Use the scenario cards at the top of the dashboard when the raw audit controls are too abstract. Each card is a concrete Bob-side decision problem with `Problem`, `Evidence`, and `Decision` text:

- `Can Bob demo safely from this laptop?`: proves the audit is local and shows the common-tier exposure before starting a node.
- `Who is actually in Bob's trust list?`: reads Bob's local `agents.json` and shows which peers are direct local trust decisions.
- `Who can write into Bob's inbox?`: filters appendable peers so Bob can review live collaboration write risk.
- `Can Carol join the project without seeing personal notes?`: inspects a task-level peer and verifies `task/` is visible while `personal/` stays hidden.
- `Should Bob promote Carol to personal?`: previews newly exposed paths and ask-context delta before editing `agents.json`.

The cards do not create a central scenario service and do not read note contents. They only set existing controls, call the existing local endpoints, and scroll to the evidence panel for that decision.

The `Concrete Question & Dialogue` panel above the cards shows the actual demo question, a short scripted conversation, and an `Evidence checks` bullet list telling Bob exactly which UI panel or value to inspect. It is intentionally synthetic and content-free: it explains what Bob asks, what the audit proves, and which panel to inspect, but it does not quote note bodies.

The checked-in demo video is generated from these cards:

```bash
python3 script/record_share_audit_demo.py
```

It records a longer Chinese-subtitled walkthrough to `demo/artifacts/share-audit-demo.mp4` and writes a matching sidecar subtitle file at `demo/artifacts/share-audit-demo.zh.srt`.

## This Computer Self-Audit

The first panel describes the local node view:

- `Audit runs on`: confirms the audit is for this computer.
- `Friends in agents.json`: counts trusted peers from the local `agents.json`.
- `Share directory`: shows the local share path being audited.
- Import zones: shows where to place files for `read-only`, `read&append`, `task`, and `personal`, plus file/folder counts.

Importing data is currently filesystem-based: put files into the intended local zone, then run the audit again to see which peers or tiers can see that zone.

## Explicit Tier Flow

1. Keep `Target source` set to `Explicit tier`.
2. Choose `common`, `task`, or `personal`.
3. Optionally set `Path filter` to a share-relative path such as `read-only` or `task`.
4. Click `Run audit`.

Use this to preview what a generic peer at a tier could list and feed into `ask`.

## Peer Flow

1. Set `Target source` to `Peer from agents file`.
2. Click `Load peers`.
3. Select a peer from `agents.json`.
4. Choose a proposed tier in `Preview tier change`.
5. Click `Run audit`.

Use this before raising a real peer from `common` to `task` or `personal`.

The `Trust Change Preview` panel shows the delta between the peer's current tier and the proposed tier:

- New zones that would become visible.
- New visible paths.
- Ask context chunk and byte increase.
- Paths only, never note contents.

## Peer Exposure Matrix

Click `Load matrix` to summarize every peer in `agents.json` at once. Each row uses the peer's normalized tier and the same content-free audit rules as the single-peer view.

Use `All`, `Personal`, and `Appendable` to filter the current matrix view without changing the underlying audit.

The matrix shows:

- `Peer`: peer display name, falling back to the public-key prefix.
- `Tier`: normalized `common`, `task`, or `personal`.
- `Visible zones`: zone names this peer can read.
- `Entries`: visible listing count only, not paths.
- `Ask chunks`: aggregate text chunk count available to `ask`/`capability`.
- `Append zones`: appendable zone names.
- `Actions`: jump from a row into that peer's audit or trust-change preview.

Use the matrix to spot broad trust posture before drilling into one peer. Rows at `personal` tier are highlighted because those peers can see `share/personal/`.

`Inspect` selects that peer, switches the dashboard to peer mode, and runs the audit. `Preview` selects that peer, keeps the next-tier proposal, and opens the trust-change preview.

Notes:

- Peers with the same tier usually have the same exposure because access is tier-based.
- The matrix intentionally omits visible path names and note contents; inspect a single peer when you need path-level detail.
- If `Path filter` is set, entry counts follow that filter, but ask chunk counts still describe the tier's full `ask` context.
- Tier changes in `agents.json` still require the receiver node to restart before a running node uses the new tier.

## How To Read The Screen

- `Visible zones`: zones this peer/tier can list, read, and include in `ask` context.
- `Visible entries`: visible directories and files after tier filtering and optional path filtering.
- `Ask chunks`: number and byte size of text chunks available to `ask`/`capability`.
- `Appendable zones`: zones where the peer can write new content.
- `Peer Exposure Matrix`: every configured peer side by side with aggregate exposure counts.
- `Inspect` / `Preview`: row actions that turn the matrix into the selected peer's detailed audit flow.
- `Risk Notes`: content-free reminders about hidden zones, append surface, and symlink guards.
- `Raw audit JSON`: same content-free result for debugging or report capture.

## Trust Checklist

Before increasing trust, verify:

- No unexpected `personal/` paths appear.
- The trust-change preview does not expose surprising new paths.
- The append surface is acceptable.
- `Ask chunks` and bytes are roughly what you expect.
- The raw JSON contains paths and counts only, not note contents.

## Troubleshooting

- Empty peer list usually means the wrong `agents.json` path.
- `missing tier/peer` means no explicit tier was selected and no peer was provided.
- Empty listing can be valid if the path filter points to a hidden zone or an empty directory.
- If the default port is busy, pass another port, for example `--port 8766`.
