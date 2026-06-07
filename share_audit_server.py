#!/usr/bin/env python3
"""
Local web UI for LinkedOut share exposure audits.

The server binds to 127.0.0.1 by default and reuses share_audit.py for all ACL
semantics. It reports paths and counts only; it never sends file contents.
"""

import argparse
import html
import ipaddress
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, Tuple
from urllib.parse import parse_qs, urlencode, urlparse, urlsplit

import agents
import share_audit


APP_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>LinkedOut Exposure Audit</title>
  <style>
    :root {
      color-scheme: light;
      --ink: #151a1f;
      --muted: #5f6b76;
      --line: #d9e0e6;
      --panel: #ffffff;
      --page: #f5f7f8;
      --soft: #eef3f6;
      --blue: #1c6fb7;
      --green: #227a52;
      --amber: #9b6416;
      --red: #b43b38;
      --shadow: 0 1px 2px rgba(21, 26, 31, 0.08);
    }

    * { box-sizing: border-box; }
    html, body {
      width: 100%;
      max-width: 100%;
      overflow-x: hidden;
    }
    body {
      margin: 0;
      background: var(--page);
      color: var(--ink);
      font: 14px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      letter-spacing: 0;
    }
    button, input, select { font: inherit; }
    .shell {
      min-height: 100vh;
      display: grid;
      grid-template-rows: auto 1fr;
      max-width: 100%;
    }
    header {
      background: #ffffff;
      border-bottom: 1px solid var(--line);
      padding: 14px 20px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      min-width: 0;
    }
    header > div { min-width: 0; max-width: 100%; }
    h1 {
      margin: 0;
      font-size: 18px;
      line-height: 1.2;
      font-weight: 700;
    }
    .subtitle {
      margin-top: 2px;
      color: var(--muted);
      font-size: 12px;
      overflow-wrap: anywhere;
    }
    .status-pill {
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 5px 10px;
      background: var(--soft);
      color: var(--muted);
      white-space: nowrap;
      font-size: 12px;
    }
    main {
      display: grid;
      grid-template-columns: 330px minmax(0, 1fr);
      gap: 16px;
      padding: 16px;
      max-width: 1360px;
      width: 100%;
      margin: 0 auto;
      min-width: 0;
    }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      min-width: 0;
    }
    .controls {
      padding: 16px;
      align-self: start;
      position: sticky;
      top: 16px;
      min-width: 0;
    }
    .section-title {
      margin: 0 0 10px;
      font-size: 12px;
      font-weight: 700;
      color: #34404b;
      text-transform: uppercase;
    }
    .field { margin-bottom: 12px; min-width: 0; }
    label {
      display: block;
      margin-bottom: 5px;
      color: #34404b;
      font-size: 12px;
      font-weight: 650;
    }
    input, select {
      width: 100%;
      min-width: 0;
      max-width: 100%;
      min-height: 36px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #ffffff;
      color: var(--ink);
      padding: 8px 10px;
      outline: none;
    }
    input:focus, select:focus {
      border-color: var(--blue);
      box-shadow: 0 0 0 3px rgba(28, 111, 183, 0.14);
    }
    .segmented {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      border: 1px solid var(--line);
      border-radius: 7px;
      overflow: hidden;
      background: #ffffff;
      min-width: 0;
    }
    .segmented button {
      min-width: 0;
      min-height: 34px;
      border: 0;
      border-right: 1px solid var(--line);
      background: #ffffff;
      color: #34404b;
      cursor: pointer;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .segmented button:last-child { border-right: 0; }
    .segmented button.active {
      background: #dcecf8;
      color: #0f4c81;
      font-weight: 700;
    }
    .row {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
    }
    .primary {
      width: 100%;
      min-height: 38px;
      border: 0;
      border-radius: 6px;
      background: var(--blue);
      color: #ffffff;
      font-weight: 700;
      cursor: pointer;
    }
    .primary:disabled, .secondary:disabled {
      cursor: wait;
      opacity: 0.68;
    }
    .secondary {
      width: 100%;
      min-height: 36px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #ffffff;
      color: #34404b;
      font-weight: 650;
      cursor: pointer;
    }
    .muted { color: var(--muted); }
    .workspace {
      display: grid;
      gap: 16px;
      min-width: 0;
    }
    .summary {
      display: grid;
      grid-template-columns: repeat(4, minmax(130px, 1fr));
      gap: 10px;
    }
    .metric {
      padding: 14px;
      min-height: 90px;
    }
    .metric .label {
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
    }
    .metric .value {
      margin-top: 8px;
      font-size: 25px;
      line-height: 1;
      font-weight: 750;
    }
    .metric .note {
      margin-top: 7px;
      color: var(--muted);
      font-size: 12px;
    }
    .grid2 {
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(280px, 0.72fr);
      gap: 16px;
      align-items: start;
    }
    .panel-head {
      padding: 13px 14px;
      border-bottom: 1px solid var(--line);
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 10px;
    }
    .panel-head h2 {
      margin: 0;
      font-size: 14px;
      line-height: 1.2;
    }
    .panel-body {
      padding: 14px;
      min-width: 0;
      overflow-x: auto;
    }
    table {
      width: 100%;
      border-collapse: collapse;
    }
    th, td {
      padding: 9px 8px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
    }
    th {
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
    }
    tr:last-child td { border-bottom: 0; }
    .badge {
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      border-radius: 999px;
      padding: 2px 8px;
      font-size: 12px;
      font-weight: 700;
      white-space: nowrap;
    }
    .ok { color: #145b3a; background: #dff1e8; }
    .warn { color: #7c4d10; background: #f7ead7; }
    .deny { color: #8e2926; background: #f6dddd; }
    .info { color: #0f4c81; background: #dcecf8; }
    .file-list {
      display: grid;
      gap: 6px;
      max-height: 420px;
      overflow: auto;
      padding-right: 2px;
    }
    .file-row {
      display: grid;
      grid-template-columns: 56px minmax(0, 1fr);
      gap: 8px;
      align-items: center;
      min-height: 34px;
      padding: 7px 8px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #ffffff;
    }
    .file-path {
      overflow-wrap: anywhere;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 12px;
    }
    .risk-list {
      display: grid;
      gap: 8px;
    }
    .risk {
      border: 1px solid var(--line);
      border-left: 4px solid var(--blue);
      border-radius: 6px;
      padding: 10px;
      background: #ffffff;
    }
    .risk.warn-line { border-left-color: var(--amber); }
    .risk.good-line { border-left-color: var(--green); }
    .risk.danger-line { border-left-color: var(--red); }
    .risk strong { display: block; margin-bottom: 3px; }
    .matrix-table {
      min-width: 760px;
    }
    .matrix-table th, .matrix-table td {
      white-space: nowrap;
    }
    .matrix-table td:first-child {
      white-space: normal;
      min-width: 150px;
    }
    .matrix-table td::before {
      display: none;
    }
    .matrix-row-personal {
      background: #fff8ee;
    }
    .zone-pills {
      display: flex;
      flex-wrap: wrap;
      gap: 4px;
    }
    .mini-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }
    .mini-button {
      min-height: 28px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #ffffff;
      color: #34404b;
      cursor: pointer;
      font-size: 12px;
      font-weight: 700;
      padding: 4px 8px;
      white-space: nowrap;
    }
    .mini-button.primary-mini {
      border-color: #b8d7ee;
      background: #e8f3fb;
      color: #0f4c81;
    }
    .mini-button:disabled {
      cursor: wait;
      opacity: 0.68;
    }
    .preview-grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
      margin-bottom: 12px;
    }
    .preview-cell {
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 10px;
      background: #ffffff;
    }
    .preview-cell .label {
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
    }
    .preview-cell .value {
      margin-top: 5px;
      font-size: 20px;
      font-weight: 750;
    }
    details {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #ffffff;
    }
    summary {
      cursor: pointer;
      padding: 12px 14px;
      font-weight: 700;
    }
    pre {
      margin: 0;
      padding: 14px;
      overflow: auto;
      border-top: 1px solid var(--line);
      font-size: 12px;
      line-height: 1.45;
      background: #fafbfc;
    }
    .error {
      border-color: #e5b1ae;
      background: #fff7f7;
      color: #87201d;
      padding: 12px 14px;
      display: none;
    }
    .empty {
      color: var(--muted);
      padding: 18px;
      text-align: center;
      border: 1px dashed var(--line);
      border-radius: 6px;
    }
    @media (max-width: 920px) {
      main { grid-template-columns: 1fr; }
      .controls { position: static; }
      .summary { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .grid2 { grid-template-columns: 1fr; }
      .preview-grid { grid-template-columns: 1fr; }
    }
    @media (max-width: 560px) {
      header { align-items: flex-start; flex-direction: column; }
      main { padding: 10px; }
      .summary { grid-template-columns: 1fr; }
      .row { grid-template-columns: 1fr; }
      .metric .value { font-size: 22px; }
      .segmented button {
        padding-left: 4px;
        padding-right: 4px;
        font-size: 12px;
      }
      .matrix-table {
        min-width: 0;
      }
      .matrix-table thead {
        display: none;
      }
      .matrix-table,
      .matrix-table tbody,
      .matrix-table tr,
      .matrix-table td {
        display: block;
        width: 100%;
      }
      .matrix-table tr {
        padding: 10px 0;
        border-bottom: 1px solid var(--line);
      }
      .matrix-table tr:last-child {
        border-bottom: 0;
      }
      .matrix-table td {
        display: grid;
        grid-template-columns: minmax(86px, 0.42fr) minmax(0, 1fr);
        gap: 10px;
        border-bottom: 0;
        white-space: normal;
      }
      .matrix-table td:first-child {
        min-width: 0;
      }
      .matrix-table td::before {
        content: attr(data-label);
        display: block;
        color: var(--muted);
        font-size: 12px;
        font-weight: 700;
      }
    }
  </style>
</head>
<body>
  <div class="shell">
    <header>
      <div>
        <h1>LinkedOut Exposure Audit</h1>
        <div class="subtitle">Content-free view of what a peer can list, read, and expose to ask context.</div>
      </div>
      <div class="status-pill" id="status" aria-live="polite">Ready</div>
    </header>
    <main>
      <aside class="panel controls">
        <p class="section-title">Audit Target</p>
        <div class="field">
          <label for="share">Share directory</label>
          <input id="share" value="{{DEFAULT_SHARE}}" autocomplete="off">
        </div>
        <div class="field">
          <label for="agentsFile">Agents file</label>
          <input id="agentsFile" value="{{DEFAULT_AGENTS_FILE}}" autocomplete="off">
        </div>
        <div class="field">
          <label for="mode">Target source</label>
          <select id="mode">
            <option value="tier">Explicit tier</option>
            <option value="peer">Peer from agents file</option>
          </select>
        </div>
        <div class="field" id="tierField">
          <label>Tier</label>
          <div class="segmented" role="group" aria-label="Tier">
            <button type="button" data-tier="common" class="active">common</button>
            <button type="button" data-tier="task">task</button>
            <button type="button" data-tier="personal">personal</button>
          </div>
        </div>
        <div class="field" id="peerField" hidden>
          <label for="peer">Peer</label>
          <select id="peer"></select>
        </div>
        <div class="field" id="previewField" hidden>
          <label for="proposedTier">Preview tier change</label>
          <select id="proposedTier">
            <option value="common">common</option>
            <option value="task">task</option>
            <option value="personal">personal</option>
          </select>
        </div>
        <div class="field">
          <label for="path">Path filter</label>
          <input id="path" placeholder="optional, e.g. read-only">
        </div>
        <div class="row">
          <button class="secondary" id="loadPeers" type="button" data-testid="load-peers">Load peers</button>
          <button class="primary" id="runAudit" type="button" data-testid="run-audit">Run audit</button>
        </div>
        <div class="field" style="margin-top: 10px;">
          <button class="secondary" id="loadMatrix" type="button" data-testid="load-matrix">Load matrix</button>
        </div>
      </aside>
      <section class="workspace">
        <div class="panel error" id="error"></div>
        <section class="summary">
          <div class="panel metric" data-testid="visible-zones-card">
            <div class="label">Visible zones</div>
            <div class="value" id="visibleZones">0</div>
            <div class="note" id="zoneNote">No audit yet</div>
          </div>
          <div class="panel metric" data-testid="visible-entries-card">
            <div class="label">Visible entries</div>
            <div class="value" id="entryCount">0</div>
            <div class="note">Directories and files</div>
          </div>
          <div class="panel metric" data-testid="ask-chunks-card">
            <div class="label">Ask chunks</div>
            <div class="value" id="chunkCount">0</div>
            <div class="note" id="byteCount">0 bytes</div>
          </div>
          <div class="panel metric" data-testid="appendable-zones-card">
            <div class="label">Appendable zones</div>
            <div class="value" id="appendZones">0</div>
            <div class="note">Writable by this peer</div>
          </div>
        </section>
        <section class="grid2">
          <div class="panel">
            <div class="panel-head">
              <h2>Zone Access</h2>
              <span class="badge info" id="tierBadge">tier: common</span>
            </div>
            <div class="panel-body">
              <table aria-label="Zone access">
                <thead>
                  <tr><th>Zone</th><th>Required</th><th>Read</th><th>Append</th></tr>
                </thead>
                <tbody id="zoneRows"></tbody>
              </table>
            </div>
          </div>
          <div class="panel">
            <div class="panel-head">
              <h2>Risk Notes</h2>
              <span class="badge ok">content-free</span>
            </div>
            <div class="panel-body">
              <div class="risk-list" id="riskList"></div>
            </div>
          </div>
        </section>
        <section class="panel">
          <div class="panel-head">
            <h2>Visible Listing</h2>
            <span class="badge info" id="listState">not run</span>
          </div>
          <div class="panel-body">
            <div class="file-list" id="fileList"></div>
          </div>
        </section>
        <section class="panel" data-testid="peer-matrix-panel">
          <div class="panel-head">
            <h2>Peer Exposure Matrix</h2>
            <span class="badge info" id="matrixState">not loaded</span>
          </div>
          <div class="panel-body">
            <table class="matrix-table" aria-label="Peer exposure matrix">
              <thead>
                <tr>
                  <th>Peer</th>
                  <th>Tier</th>
                  <th>Visible zones</th>
                  <th>Entries</th>
                  <th>Ask chunks</th>
                  <th>Append zones</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody id="matrixRows">
                <tr><td colspan="7" class="muted">Load peers or run an audit to populate the matrix.</td></tr>
              </tbody>
            </table>
          </div>
        </section>
        <section class="panel" id="previewPanel" hidden>
          <div class="panel-head">
            <h2>Trust Change Preview</h2>
            <span class="badge warn" id="previewState">peer mode</span>
          </div>
          <div class="panel-body">
            <div class="preview-grid">
              <div class="preview-cell">
                <div class="label">Tier change</div>
                <div class="value" id="previewTier">-</div>
              </div>
              <div class="preview-cell">
                <div class="label">New zones</div>
                <div class="value" id="previewZones">0</div>
              </div>
              <div class="preview-cell">
                <div class="label">New entries</div>
                <div class="value" id="previewEntries">0</div>
              </div>
              <div class="preview-cell">
                <div class="label">Ask delta</div>
                <div class="value" id="previewAskDelta">+0</div>
              </div>
            </div>
            <div class="muted" id="previewBytes">+0 bytes, paths only</div>
            <div class="file-list" id="previewList"></div>
          </div>
        </section>
        <details>
          <summary>Raw audit JSON</summary>
          <pre id="rawJson">{}</pre>
        </details>
      </section>
    </main>
  </div>
  <script>
    const els = {
      status: document.getElementById("status"),
      error: document.getElementById("error"),
      share: document.getElementById("share"),
      agentsFile: document.getElementById("agentsFile"),
      mode: document.getElementById("mode"),
      peer: document.getElementById("peer"),
      peerField: document.getElementById("peerField"),
      previewField: document.getElementById("previewField"),
      proposedTier: document.getElementById("proposedTier"),
      tierField: document.getElementById("tierField"),
      path: document.getElementById("path"),
      zoneRows: document.getElementById("zoneRows"),
      fileList: document.getElementById("fileList"),
      riskList: document.getElementById("riskList"),
      visibleZones: document.getElementById("visibleZones"),
      zoneNote: document.getElementById("zoneNote"),
      entryCount: document.getElementById("entryCount"),
      chunkCount: document.getElementById("chunkCount"),
      byteCount: document.getElementById("byteCount"),
      appendZones: document.getElementById("appendZones"),
      tierBadge: document.getElementById("tierBadge"),
      listState: document.getElementById("listState"),
      rawJson: document.getElementById("rawJson"),
      previewPanel: document.getElementById("previewPanel"),
      previewState: document.getElementById("previewState"),
      previewTier: document.getElementById("previewTier"),
      previewZones: document.getElementById("previewZones"),
      previewEntries: document.getElementById("previewEntries"),
      previewAskDelta: document.getElementById("previewAskDelta"),
      previewBytes: document.getElementById("previewBytes"),
      previewList: document.getElementById("previewList"),
      matrixState: document.getElementById("matrixState"),
      matrixRows: document.getElementById("matrixRows"),
      runAudit: document.getElementById("runAudit"),
      loadPeers: document.getElementById("loadPeers"),
      loadMatrix: document.getElementById("loadMatrix")
    };
    let selectedTier = "common";
    let peerTiers = {};
    let matrixRows = [];

    function setStatus(text) { els.status.textContent = text; }
    function showError(text) {
      els.error.style.display = text ? "block" : "none";
      els.error.textContent = text || "";
    }
    function badge(text, cls) {
      return `<span class="badge ${cls}">${text}</span>`;
    }
    function htmlEscape(value) {
      return String(value).replace(/[&<>"']/g, ch => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
      }[ch]));
    }
    function params(values) {
      const qs = new URLSearchParams();
      Object.entries(values).forEach(([key, value]) => {
        if (value !== undefined && value !== null && value !== "") qs.set(key, value);
      });
      return qs.toString();
    }
    function setMode() {
      const byPeer = els.mode.value === "peer";
      els.peerField.hidden = !byPeer;
      els.previewField.hidden = !byPeer;
      els.previewPanel.hidden = !byPeer;
      els.tierField.hidden = byPeer;
    }
    function setBusy(isBusy) {
      els.runAudit.disabled = isBusy;
      els.loadPeers.disabled = isBusy;
      els.loadMatrix.disabled = isBusy;
      document.querySelectorAll("[data-matrix-action]").forEach(button => {
        button.disabled = isBusy;
      });
    }
    function selectTier(tier) {
      const allowed = ["common", "task", "personal"];
      selectedTier = allowed.includes(tier) ? tier : "common";
      document.querySelectorAll("[data-tier]").forEach(button => {
        button.classList.toggle("active", button.dataset.tier === selectedTier);
      });
    }
    function nextTier(tier) {
      if (tier === "common") return "task";
      if (tier === "task") return "personal";
      return "personal";
    }
    function updateProposedTier() {
      const current = peerTiers[els.peer.value] || "common";
      els.proposedTier.value = nextTier(current);
    }
    function setProposedTier(tier) {
      const allowed = ["common", "task", "personal"];
      if (allowed.includes(tier)) els.proposedTier.value = tier;
    }
    async function apiGet(path, query) {
      const res = await fetch(`${path}?${params(query)}`);
      const data = await res.json();
      if (!res.ok || data.ok === false) {
        throw new Error(data.error || `HTTP ${res.status}`);
      }
      return data;
    }
    async function loadPeers() {
      showError("");
      setStatus("Loading peers");
      try {
        const data = await apiGet("/api/agents", { agents_file: els.agentsFile.value });
        els.peer.innerHTML = "";
        peerTiers = {};
        if (!data.peers.length) {
          const opt = document.createElement("option");
          opt.value = "";
          opt.textContent = "No peers in agents file";
          els.peer.appendChild(opt);
        } else {
          data.peers.forEach(peer => {
            peerTiers[peer.pubkey] = peer.tier;
            const opt = document.createElement("option");
            opt.value = peer.pubkey;
            opt.textContent = `${peer.name || peer.pubkey.slice(0, 8)} — ${peer.tier}`;
            els.peer.appendChild(opt);
          });
          updateProposedTier();
        }
        setStatus(`Loaded ${data.peers.length} peers`);
      } catch (err) {
        showError(err.message);
        setStatus("Peer load failed");
      }
    }
    function renderAudit(data) {
      const audit = data.audit;
      const zones = audit.zones || [];
      const entries = (audit.list && audit.list.entries) || [];
      const visible = zones.filter(z => z.visible);
      const appendable = zones.filter(z => z.append);
      els.visibleZones.textContent = visible.length;
      els.zoneNote.textContent = visible.map(z => z.zone).join(", ") || "none";
      els.entryCount.textContent = entries.length;
      els.chunkCount.textContent = audit.ask_context.text_chunks;
      els.byteCount.textContent = `${audit.ask_context.bytes} / ${audit.ask_context.limit_bytes} bytes`;
      els.appendZones.textContent = appendable.length;
      els.tierBadge.textContent = `tier: ${audit.tier}`;
      els.listState.textContent = audit.list.ok ? "ok" : audit.list.error;
      els.listState.className = `badge ${audit.list.ok ? "ok" : "deny"}`;
      els.zoneRows.innerHTML = zones.map(zone => `
        <tr>
          <td><code>${htmlEscape(zone.zone)}/</code></td>
          <td>${htmlEscape(zone.requires)}</td>
          <td>${zone.read ? badge("yes", "ok") : badge("hidden", "deny")}</td>
          <td>${zone.append ? badge("yes", "warn") : badge("no", zone.visible ? "info" : "deny")}</td>
        </tr>
      `).join("");
      if (!audit.list.ok) {
        els.fileList.innerHTML = `<div class="empty">${htmlEscape(audit.list.error)}</div>`;
      } else if (!entries.length) {
        els.fileList.innerHTML = `<div class="empty">No visible entries</div>`;
      } else {
        els.fileList.innerHTML = entries.map(entry => `
          <div class="file-row">
            <span class="badge ${entry.kind === "dir" ? "info" : "ok"}">${entry.kind}</span>
            <span class="file-path">${htmlEscape(entry.path)}</span>
          </div>
        `).join("");
      }
      const risks = [];
      risks.push({ cls: "good-line", title: "No contents in UI", body: "This dashboard receives paths and aggregate ask-context counts only." });
      risks.push({ cls: "good-line", title: "Zone symlink guard", body: "Ask/capability context skips symlinks whose real path leaves the scanned zone." });
      if (appendable.length) {
        risks.push({ cls: "warn-line", title: "Append surface", body: `${appendable.map(z => z.zone + "/").join(", ")} accepts peer appends at this tier.` });
      }
      const hidden = zones.filter(z => !z.visible);
      if (hidden.length) {
        risks.push({ cls: "good-line", title: "Hidden zones", body: `${hidden.map(z => z.zone + "/").join(", ")} will not be listed or included in ask context.` });
      } else {
        risks.push({ cls: "danger-line", title: "Full tier", body: "This target can see every zone, including personal/." });
      }
      els.riskList.innerHTML = risks.map(risk => `
        <div class="risk ${risk.cls}">
          <strong>${htmlEscape(risk.title)}</strong>
          <span class="muted">${htmlEscape(risk.body)}</span>
        </div>
      `).join("");
      els.rawJson.textContent = JSON.stringify(data, null, 2);
    }
    function renderPreview(data) {
      const preview = data.preview;
      const exposed = preview.newly_exposed;
      const delta = preview.ask_context.delta;
      els.previewPanel.hidden = false;
      els.previewState.textContent = `${preview.current_tier} → ${preview.proposed_tier}`;
      els.previewState.className = `badge ${exposed.entries.length || exposed.zones.length ? "warn" : "ok"}`;
      els.previewTier.textContent = `${preview.current_tier} → ${preview.proposed_tier}`;
      els.previewZones.textContent = exposed.zones.length;
      els.previewEntries.textContent = exposed.entries.length;
      els.previewAskDelta.textContent = `${delta.chunks >= 0 ? "+" : ""}${delta.chunks}`;
      els.previewBytes.textContent = `${delta.bytes >= 0 ? "+" : ""}${delta.bytes} bytes, ${preview.current_ask.chunks} → ${preview.proposed_ask.chunks} ask chunks`;
      if (!exposed.entries.length && !exposed.zones.length) {
        els.previewList.innerHTML = `<div class="empty">No newly exposed paths at this proposed tier</div>`;
      } else {
        const zoneRows = exposed.zones.map(zone => `
          <div class="file-row">
            <span class="badge warn">zone</span>
            <span class="file-path">${htmlEscape(zone)}/ becomes visible</span>
          </div>
        `).join("");
        const entryRows = exposed.entries.map(entry => `
          <div class="file-row">
            <span class="badge ${entry.kind === "dir" ? "info" : "ok"}">${entry.kind}</span>
            <span class="file-path">${htmlEscape(entry.path)}</span>
          </div>
        `).join("");
        els.previewList.innerHTML = zoneRows + entryRows;
      }
    }
    function renderMatrix(data) {
      const rows = data.matrix || [];
      matrixRows = rows;
      els.matrixState.textContent = `${rows.length} peers`;
      els.matrixState.className = `badge ${rows.some(row => row.tier === "personal") ? "warn" : "ok"}`;
      if (!rows.length) {
        els.matrixRows.innerHTML = `<tr><td colspan="7" class="muted">No peers in agents file</td></tr>`;
        return;
      }
      els.matrixRows.innerHTML = rows.map((row, index) => {
        const zones = row.visible_zones.map(zone => `<span class="badge ${zone === "personal" ? "warn" : "info"}">${htmlEscape(zone)}</span>`).join("");
        const append = row.append_zones.length ? row.append_zones.map(zone => `<span class="badge warn">${htmlEscape(zone)}</span>`).join("") : `<span class="badge info">none</span>`;
        const label = row.name || row.pubkey.slice(0, 12);
        return `
          <tr class="${row.tier === "personal" ? "matrix-row-personal" : ""}">
            <td data-label="Peer">${htmlEscape(label)}<div class="muted">${htmlEscape(row.pubkey.slice(0, 16))}...</div></td>
            <td data-label="Tier">${badge(row.tier, row.tier === "personal" ? "warn" : "info")}</td>
            <td data-label="Visible zones"><div class="zone-pills">${zones}</div></td>
            <td data-label="Entries">${row.entry_count}</td>
            <td data-label="Ask chunks">${row.ask_context.chunks}</td>
            <td data-label="Append zones"><div class="zone-pills">${append}</div></td>
            <td data-label="Actions">
              <div class="mini-actions">
                <button class="mini-button primary-mini" type="button" data-matrix-action="inspect" data-row-index="${index}">Inspect</button>
                <button class="mini-button" type="button" data-matrix-action="preview" data-row-index="${index}">Preview</button>
              </div>
            </td>
          </tr>
        `;
      }).join("");
    }
    async function loadMatrix() {
      const data = await apiGet("/api/matrix", {
        share: els.share.value,
        agents_file: els.agentsFile.value,
        path: els.path.value
      });
      renderMatrix(data);
    }
    async function runPreview() {
      if (els.mode.value !== "peer" || !els.peer.value) return;
      const preview = await apiGet("/api/preview-tier-change", {
        share: els.share.value,
        agents_file: els.agentsFile.value,
        peer_pubkey: els.peer.value,
        proposed_tier: els.proposedTier.value,
        path: els.path.value
      });
      renderPreview(preview);
    }
    async function runAudit(options = {}) {
      showError("");
      setStatus("Auditing");
      setBusy(true);
      const query = {
        share: els.share.value,
        agents_file: els.agentsFile.value,
        path: els.path.value
      };
      if (els.mode.value === "peer") query.peer_pubkey = els.peer.value;
      else query.tier = selectedTier;
      try {
        const data = await apiGet("/api/audit", query);
        renderAudit(data);
        if (options.skipPreview) {
          els.previewPanel.hidden = true;
        } else {
          await runPreview();
        }
        await loadMatrix();
        setStatus("Audit complete");
      } catch (err) {
        showError(err.message);
        setStatus("Audit failed");
      } finally {
        setBusy(false);
      }
    }
    document.querySelectorAll("[data-tier]").forEach(button => {
      button.addEventListener("click", () => {
        selectTier(button.dataset.tier);
      });
    });
    els.mode.addEventListener("change", setMode);
    els.peer.addEventListener("change", () => {
      updateProposedTier();
      runPreview().catch(err => showError(err.message));
    });
    els.proposedTier.addEventListener("change", () => {
      if (els.mode.value === "peer" && els.peer.value) {
        runPreview().catch(err => showError(err.message));
      }
    });
    els.loadPeers.addEventListener("click", loadPeers);
    els.loadMatrix.addEventListener("click", () => {
      showError("");
      setStatus("Loading matrix");
      setBusy(true);
      loadMatrix()
        .then(() => setStatus("Matrix loaded"))
        .catch(err => {
          showError(err.message);
          setStatus("Matrix failed");
        })
        .finally(() => setBusy(false));
    });
    async function runMatrixAction(pubkey, action) {
      showError("");
      setStatus(action === "preview" ? "Previewing peer" : "Inspecting peer");
      setBusy(true);
      try {
        els.mode.value = "peer";
        setMode();
        if (!peerTiers[pubkey]) await loadPeers();
        els.peer.value = pubkey;
        updateProposedTier();
        await runAudit({ skipPreview: action === "inspect" });
        const target = action === "preview"
          ? els.previewPanel
          : document.querySelector("#fileList").closest(".panel");
        if (target) target.scrollIntoView({ block: "start" });
        setStatus(action === "preview" ? "Preview ready" : "Peer audit complete");
      } catch (err) {
        showError(err.message);
        setStatus("Matrix action failed");
      } finally {
        setBusy(false);
      }
    }
    els.matrixRows.addEventListener("click", event => {
      const button = event.target.closest("[data-matrix-action]");
      if (!button) return;
      const row = matrixRows[Number(button.dataset.rowIndex)];
      if (!row) return;
      runMatrixAction(row.pubkey, button.dataset.matrixAction);
    });
    els.runAudit.addEventListener("click", runAudit);
    async function init() {
      const qs = new URLSearchParams(window.location.search);
      if (qs.has("share")) els.share.value = qs.get("share");
      if (qs.has("agents_file")) els.agentsFile.value = qs.get("agents_file");
      if (qs.has("path")) els.path.value = qs.get("path");
      if (qs.has("tier")) selectTier(qs.get("tier"));
      if (qs.has("peer_pubkey")) {
        els.mode.value = "peer";
        setMode();
        await loadPeers();
        els.peer.value = qs.get("peer_pubkey");
        if (qs.has("proposed_tier")) setProposedTier(qs.get("proposed_tier"));
      } else {
        setMode();
      }
      await runAudit();
    }
    init();
  </script>
</body>
</html>
"""


def _security_headers(handler: BaseHTTPRequestHandler, html_page: bool = False) -> None:
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("Referrer-Policy", "no-referrer")
    handler.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if html_page:
        handler.send_header(
            "Content-Security-Policy",
            "default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'; "
            "connect-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'",
        )


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: Dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    _security_headers(handler)
    handler.end_headers()
    handler.wfile.write(body)


def _html_response(handler: BaseHTTPRequestHandler, body: str) -> None:
    raw = body.encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(raw)))
    _security_headers(handler, html_page=True)
    handler.end_headers()
    handler.wfile.write(raw)


def _first(qs: Dict[str, list], key: str, default: str = "") -> str:
    values = qs.get(key)
    return values[0] if values else default


def _agent_payload(agents_file: str) -> Dict:
    loaded = agents.load(agents_file)
    peers = []
    for pubkey, meta in loaded.items():
        peers.append({
            "pubkey": pubkey,
            "name": meta.get("name", ""),
            "tier": agents.get_tier(meta),
        })
    peers.sort(key=lambda p: (p["name"] or p["pubkey"]))
    return {"ok": True, "agents_file": os.path.realpath(agents_file), "peers": peers}


def _host_from_header(value: str) -> str:
    if not value:
        return ""
    return (urlsplit("//" + value).hostname or "").lower().rstrip(".")


def _is_loopback_host(host: str) -> bool:
    if host in ("", "localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _safe_html_default(value: str) -> str:
    return html.escape(value, quote=True)


def _audit_payload(qs: Dict[str, list]) -> Tuple[int, Dict]:
    share = _first(qs, "share", "share")
    agents_file = _first(qs, "agents_file", agents.DEFAULT_PATH)
    tier = _first(qs, "tier", None)
    peer_pubkey = _first(qs, "peer_pubkey", None)
    path = _first(qs, "path", "")
    try:
        peer = share_audit.resolve_peer(peer_pubkey, agents_file, tier)
        audit = share_audit.build_audit(share, peer["tier"], path)
    except ValueError as e:
        return 400, {"ok": False, "error": str(e)}
    return 200, {"ok": True, "peer": peer, "audit": audit}


def _entry_key(entry: Dict) -> Tuple[str, str]:
    return entry.get("path", ""), entry.get("kind", "")


def _preview_payload(qs: Dict[str, list]) -> Tuple[int, Dict]:
    share = _first(qs, "share", "share")
    agents_file = _first(qs, "agents_file", agents.DEFAULT_PATH)
    peer_pubkey = _first(qs, "peer_pubkey", None)
    proposed_tier = _first(qs, "proposed_tier", None)
    path = _first(qs, "path", "")
    if not proposed_tier:
        return 400, {"ok": False, "error": "missing_proposed_tier"}
    try:
        peer = share_audit.resolve_peer(peer_pubkey, agents_file, tier=None)
        proposed_tier = agents.normalize_tier(proposed_tier)
        current = share_audit.build_audit(share, peer["tier"], path)
        proposed = share_audit.build_audit(share, proposed_tier, path)
    except ValueError as e:
        return 400, {"ok": False, "error": str(e)}

    current_entries = {_entry_key(entry) for entry in current["list"]["entries"]}
    new_entries = [
        entry for entry in proposed["list"]["entries"]
        if _entry_key(entry) not in current_entries
    ]
    current_visible_zones = {zone["zone"] for zone in current["zones"] if zone["visible"]}
    new_zones = [
        zone["zone"] for zone in proposed["zones"]
        if zone["visible"] and zone["zone"] not in current_visible_zones
    ]
    current_append_zones = {zone["zone"] for zone in current["zones"] if zone["append"]}
    newly_appendable = [
        zone["zone"] for zone in proposed["zones"]
        if zone["append"] and zone["zone"] not in current_append_zones
    ]
    current_chunks = current["ask_context"]["text_chunks"]
    proposed_chunks = proposed["ask_context"]["text_chunks"]
    current_bytes = current["ask_context"]["bytes"]
    proposed_bytes = proposed["ask_context"]["bytes"]
    preview = {
        "current_tier": current["tier"],
        "proposed_tier": proposed["tier"],
        "path": path,
        "newly_exposed": {
            "zones": new_zones,
            "entries": new_entries,
            "appendable_zones": newly_appendable,
        },
        "current_ask": {
            "chunks": current_chunks,
            "bytes": current_bytes,
        },
        "proposed_ask": {
            "chunks": proposed_chunks,
            "bytes": proposed_bytes,
        },
        "ask_context": {
            "delta": {
                "chunks": proposed_chunks - current_chunks,
                "bytes": proposed_bytes - current_bytes,
            },
            "content_included": False,
        },
    }
    return 200, {
        "ok": True,
        "peer": peer,
        "preview": preview,
    }


def _matrix_payload(qs: Dict[str, list]) -> Tuple[int, Dict]:
    share = _first(qs, "share", "share")
    agents_file = _first(qs, "agents_file", agents.DEFAULT_PATH)
    path = _first(qs, "path", "")
    loaded = agents.load(agents_file)
    tier_audits = {}
    rows = []
    try:
        for pubkey, meta in sorted(loaded.items(), key=lambda item: (item[1].get("name", ""), item[0])):
            tier = agents.get_tier(meta)
            if tier not in tier_audits:
                tier_audits[tier] = share_audit.build_audit(share, tier, path)
            audit = tier_audits[tier]
            visible_zones = [zone["zone"] for zone in audit["zones"] if zone["visible"]]
            append_zones = [zone["zone"] for zone in audit["zones"] if zone["append"]]
            rows.append({
                "pubkey": pubkey,
                "name": meta.get("name", ""),
                "tier": tier,
                "visible_zones": visible_zones,
                "visible_zone_count": len(visible_zones),
                "entry_count": len(audit["list"]["entries"]) if audit["list"]["ok"] else 0,
                "list_ok": audit["list"]["ok"],
                "list_error": audit["list"]["error"],
                "ask_context": {
                    "chunks": audit["ask_context"]["text_chunks"],
                    "content_included": False,
                },
                "append_zones": append_zones,
                "append_zone_count": len(append_zones),
                "personal_tier": tier == "personal",
            })
    except ValueError as e:
        return 400, {"ok": False, "error": str(e)}
    return 200, {
        "ok": True,
        "share": os.path.realpath(share),
        "agents_file": os.path.realpath(agents_file),
        "path": path,
        "peer_count": len(rows),
        "matrix": rows,
    }


class AuditHandler(BaseHTTPRequestHandler):
    default_share = "share"
    default_agents_file = agents.DEFAULT_PATH
    allowed_hosts = frozenset({"127.0.0.1", "::1", "localhost"})

    def log_message(self, fmt: str, *args) -> None:
        print(f"[share-audit-ui] {self.address_string()} - {fmt % args}")

    def do_GET(self) -> None:
        host = _host_from_header(self.headers.get("Host", ""))
        if host and host not in self.allowed_hosts and not _is_loopback_host(host):
            _json_response(self, 403, {"ok": False, "error": "host_not_allowed"})
            return
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        if parsed.path in ("/", "/index.html"):
            defaults = {
                "DEFAULT_SHARE": self.default_share,
                "DEFAULT_AGENTS_FILE": self.default_agents_file,
            }
            body = APP_HTML
            for key, value in defaults.items():
                body = body.replace("{{" + key + "}}", _safe_html_default(value))
            _html_response(self, body)
            return
        if parsed.path == "/api/agents":
            agents_file = _first(qs, "agents_file", self.default_agents_file)
            _json_response(self, 200, _agent_payload(agents_file))
            return
        if parsed.path == "/api/audit":
            if "share" not in qs:
                qs["share"] = [self.default_share]
            if "agents_file" not in qs:
                qs["agents_file"] = [self.default_agents_file]
            status, payload = _audit_payload(qs)
            _json_response(self, status, payload)
            return
        if parsed.path in ("/api/preview", "/api/preview-tier-change"):
            if "share" not in qs:
                qs["share"] = [self.default_share]
            if "agents_file" not in qs:
                qs["agents_file"] = [self.default_agents_file]
            status, payload = _preview_payload(qs)
            _json_response(self, status, payload)
            return
        if parsed.path == "/api/matrix":
            if "share" not in qs:
                qs["share"] = [self.default_share]
            if "agents_file" not in qs:
                qs["agents_file"] = [self.default_agents_file]
            status, payload = _matrix_payload(qs)
            _json_response(self, status, payload)
            return
        _json_response(self, 404, {"ok": False, "error": "not_found"})


def make_server(host: str, port: int, default_share: str, default_agents_file: str) -> ThreadingHTTPServer:
    class ConfiguredAuditHandler(AuditHandler):
        pass

    ConfiguredAuditHandler.default_share = default_share
    ConfiguredAuditHandler.default_agents_file = default_agents_file
    allowed = {"127.0.0.1", "::1", "localhost"}
    normalized_host = host.lower().rstrip(".")
    if normalized_host and normalized_host not in ("0.0.0.0", "::"):
        allowed.add(normalized_host)
    ConfiguredAuditHandler.allowed_hosts = frozenset(allowed)
    return ThreadingHTTPServer((host, port), ConfiguredAuditHandler)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the LinkedOut share exposure audit web UI")
    parser.add_argument("--host", default="127.0.0.1", help="bind host")
    parser.add_argument("--port", type=int, default=8765, help="bind port")
    parser.add_argument("--share", default="share", help="default share path")
    parser.add_argument("--agents-file", default=agents.DEFAULT_PATH, help="default agents file")
    args = parser.parse_args()

    server = make_server(args.host, args.port, args.share, args.agents_file)
    query = urlencode({"share": args.share, "agents_file": args.agents_file, "tier": "common"})
    print(f"LinkedOut exposure audit UI: http://{args.host}:{args.port}/?{query}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nAudit UI stopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
