#!/usr/bin/env python3
"""
LinkedOut 安全回歸測試（不需 ollama / 不開網路）。

守住兩條 demo 主張：
  1. 信任白名單 fail-closed：空白名單 / 未授權寄件者 → 一律拒收（不解密、不處理）。
  2. S4 路由的 tier ACL：以「上游寄件者的 tier」取 context；common 上游拿不到 personal。

跑法：.venv/bin/python test_security.py
"""
import asyncio
import contextlib
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import e2ee          # noqa: E402
import agents        # noqa: E402
import app_layer     # noqa: E402
import p2p_node      # noqa: E402


def _mknode(trust=None, agent_meta=None):
    d = tempfile.mkdtemp()
    priv = e2ee.load_or_create_identity(os.path.join(d, "k"))
    share = os.path.join(d, "share")
    app_layer.ensure_share(share)
    return p2p_node.P2PNode(port=0, priv=priv, trust=set(trust or []),
                            share=share, agent_meta=agent_meta or {})


def _deliver(recv, send, payload, ptype="REQUEST"):
    """send 加密一個封包給 recv，呼叫 recv.handle_incoming，回傳它印出的文字。"""
    pkt = send.build_packet(recv.my_pubkey, ptype, payload)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        asyncio.run(recv.handle_incoming(pkt))
    return buf.getvalue()


# ── 1. fail-closed 信任白名單 ─────────────────────────────
def test_empty_whitelist_rejects():
    recv = _mknode(trust=[])                     # 空白名單
    send = _mknode()
    out = _deliver(recv, send, app_layer.make_request("ask", query="hi"))
    assert "Reject" in out, out
    assert "Decrypted" not in out, "空白名單不該解密任何封包：\n" + out
    print("✅ 空白名單 → 拒收（fail-closed，未解密）")


def test_unknown_sender_rejected():
    recv = _mknode(trust=["ab" * 32])            # 信任別人，但不信任這個 sender
    send = _mknode()
    out = _deliver(recv, send, app_layer.make_request("ask", query="hi"))
    assert "Reject" in out and "Decrypted" not in out, out
    print("✅ 未授權寄件者 → 拒收")


def test_trusted_sender_passes_gate():
    send = _mknode()
    recv = _mknode(trust=[send.my_pubkey])       # 信任 sender → 應通過白名單關卡並解密
    out = _deliver(recv, send, app_layer.make_request("list"))
    assert "Reject" not in out and "Decrypted" in out, out
    print("✅ 受信任寄件者 → 通過白名單、成功解密")


# ── 2. S4 路由 tier ACL（context 以上游 tier 過濾）──────────
def _seed_share():
    d = tempfile.mkdtemp()
    share = os.path.join(d, "share")
    app_layer.ensure_share(share)
    with open(os.path.join(share, "read-only", "n.md"), "w", encoding="utf-8") as f:
        f.write("PUBLIC_FACT alpha")
    with open(os.path.join(share, "personal", "s.md"), "w", encoding="utf-8") as f:
        f.write("SECRET_FACT zeta")
    return share


def test_s4_common_upstream_excludes_personal():
    share = _seed_share()
    upstream = "cd" * 32
    meta_common = {upstream: {"name": "Carol", "tier": "common"}}
    tier = agents.get_tier(meta_common[upstream])     # = S4 _handle_route_query 取 tier 的方式
    ctx = "\n".join(app_layer._collect_ask_context(share, tier))
    assert "PUBLIC_FACT" in ctx, ctx
    assert "SECRET_FACT" not in ctx, "common 上游不該看到 personal：\n" + ctx
    print("✅ S4 common 上游 → context 只含 read-only，排除 personal")


def test_s4_personal_upstream_includes_personal():
    share = _seed_share()
    upstream = "cd" * 32
    meta_personal = {upstream: {"tier": "personal"}}
    tier = agents.get_tier(meta_personal[upstream])
    ctx = "\n".join(app_layer._collect_ask_context(share, tier))
    assert "SECRET_FACT" in ctx, ctx
    print("✅ S4 personal 上游 → context 含 personal")


def test_unknown_upstream_defaults_common():
    share = _seed_share()
    tier = agents.get_tier({})                        # 不在 agent_meta / 沒 tier 欄 → 預設 common
    ctx = "\n".join(app_layer._collect_ask_context(share, tier))
    assert "SECRET_FACT" not in ctx, "預設 tier 必須是 common（不洩 personal）：\n" + ctx
    print("✅ 未知上游 → 預設 common（不洩 personal）")


def test_ask_context_skips_symlink_escape():
    share = _seed_share()
    outside = os.path.join(tempfile.mkdtemp(), "outside_secret.md")
    with open(outside, "w", encoding="utf-8") as f:
        f.write("OUTSIDE_SECRET omega")
    os.symlink(outside, os.path.join(share, "read-only", "leak.md"))
    os.symlink(os.path.join(share, "personal", "s.md"),
               os.path.join(share, "read-only", "zone_leak.md"))
    ctx = "\n".join(app_layer._collect_ask_context(share, "personal"))
    assert "PUBLIC_FACT" in ctx, ctx
    assert "OUTSIDE_SECRET" not in ctx, "ask context 不該跟隨 share/ 外的 symlink：\n" + ctx
    assert "read-only/zone_leak.md" not in ctx, "ask context 不該跟隨跨 zone 的 symlink：\n" + ctx
    print("✅ ask/capability context → 跳過 share/ 外 symlink")


if __name__ == "__main__":
    test_empty_whitelist_rejects()
    test_unknown_sender_rejected()
    test_trusted_sender_passes_gate()
    test_s4_common_upstream_excludes_personal()
    test_s4_personal_upstream_includes_personal()
    test_unknown_upstream_defaults_common()
    test_ask_context_skips_symlink_escape()
    print("\n🎉 ALL SECURITY TESTS PASSED")
