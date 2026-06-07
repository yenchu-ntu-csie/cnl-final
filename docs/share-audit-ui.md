# Share Audit UI

Use this local dashboard before starting a node, changing a peer's tier, or demoing tiered disclosure.

```bash
python3 share_audit_server.py --share share --agents-file agents.json
```

Open the printed `http://127.0.0.1:...` URL. The server is local by default and the UI/API return paths and counts only, never file contents.

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
