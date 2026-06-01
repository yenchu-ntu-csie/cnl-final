#!/usr/bin/env python3
"""
智慧路由(reputation-targeted)邏輯測試 — 純邏輯，不需 ollama / 不開網路。

驗證：
  1. topic_keywords / rep_score / bump_rep 基本行為。
  2. 冷啟動(沒聲望) → _pick_next_hops 回傳全部(flood 學習)。
  3. 學到聲望後 → 只往「該主題分高」的 next-hop(+1 探索)，不再全 flood。
  4. _credit 同時更新 in-memory 與寫回 agents.json。

跑法：.venv/bin/python test_routing.py
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import e2ee        # noqa: E402
import agents      # noqa: E402
import app_layer   # noqa: E402
import p2p_node    # noqa: E402


def test_keyword_and_score():
    kws = agents.topic_keywords("How to fix CUDA 11.4 driver on the K40 GPU")
    assert "cuda" in kws or "11.4" in kws, kws
    assert agents.rep_score({"rep": {"cuda": 3}}, ["cuda", "gpu"]) == 3
    assert agents.rep_score({}, ["cuda"]) == 0
    print("✅ topic_keywords / rep_score 基本行為")


def _mknode(trust, agent_meta, agents_file):
    d = tempfile.mkdtemp()
    priv = e2ee.load_or_create_identity(os.path.join(d, "k"))
    share = os.path.join(d, "share"); app_layer.ensure_share(share)
    return p2p_node.P2PNode(port=0, priv=priv, trust=set(trust), share=share,
                            agent_meta=agent_meta, agents_file=agents_file)


def test_cold_then_targeted():
    A, B, C = "a" * 64, "b" * 64, "c" * 64
    af = os.path.join(tempfile.mkdtemp(), "agents.json")
    agents.add(A, "A", af); agents.add(B, "B", af); agents.add(C, "C", af)
    meta = agents.load(af)
    node = _mknode([A, B, C], meta, af)
    kws = ["cuda", "driver"]

    # 冷啟動：沒聲望 → flood 全部
    chosen, mode = node._pick_next_hops([A, B, C], kws)
    assert mode == "cold-flood" and set(chosen) == {A, B, C}, (mode, chosen)
    print("✅ 冷啟動 → flood 全部學習")

    # 模擬「A 把 cuda/driver 的答案帶回來」→ 學習
    node._credit(A, kws)
    assert node.agent_meta[A]["rep"].get("cuda") == 1.0          # in-memory
    assert agents.load(af)[A]["rep"].get("cuda") == 1.0          # 已寫回檔案
    print("✅ _credit 更新 in-memory + 寫回 agents.json")

    # 學到後：只往 A(+1 探索)，不再全 flood
    chosen, mode = node._pick_next_hops([A, B, C], kws)
    assert mode == "targeted+explore", mode
    assert A in chosen and len(chosen) < 3, chosen
    print(f"✅ 學到後 → targeted：{len(chosen)}/3（A 必中 + 1 探索），訊息數下降")

    # 不同主題仍冷啟動（聲望是 per-topic）
    chosen2, mode2 = node._pick_next_hops([A, B, C], ["network", "tailscale"])
    assert mode2 == "cold-flood", mode2
    print("✅ 不同主題 → 仍冷啟動（聲望是 per-topic，不會亂套用）")


if __name__ == "__main__":
    test_keyword_and_score()
    test_cold_then_targeted()
    print("\n🎉 smart-routing 邏輯測試通過")
