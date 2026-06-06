"""
LinkedOut 應用層 (L3)：解析「解密後的 JSON」、在 share/ 內執行檔案操作、組裝回應。

與網路層 (p2p_node) 解耦 —— 這裡只處理明文 payload (dict) 的進出，
完全不碰加密、relay、socket。流程：
    p2p_node 解密封包 → 把明文 payload 丟進來 → 拿回要回傳的 payload（再由 p2p_node 加密送出）

封包外層 type 對應：
    "REQUEST"  → handle_request()   檢查權限/路徑 → 執行 read/append/list → 回傳 RESPONSE payload
    "RESPONSE" → handle_response()  解析 → 顯示結果

── 檔案分享規則 ──────────────────────────────────────────
雙方本地都有一個 share/ 資料夾，底下分兩區（zone）：
    share/read-only/      對方只能 read，不能 append
    share/read&append/    對方可以 read，也可以 append

支援的操作（op）：
    read    讀一個檔（兩區皆可）
    append  追加到一個檔（只限 read&append/）
    list    看 share/ 的資料夾結構（不帶 path 列整個 share，帶 path 列該子目錄）

規則檢查（在實際動檔案前）：
    1. 路徑安全：解析後必須仍在 share/ 內（擋 ../ 與絕對路徑逃逸）→ 否則 path_denied
    2. read/append 必須落在某個 zone 內 → 否則 not_shared
    3. append 只允許在 read&append/ → 否則 permission_denied
    （list 只做路徑安全檢查，可看到整個 share/ 的結構）
"""

import os
import uuid
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel

import ai_client
from agents import TIERS, DEFAULT_TIER, normalize_tier

# 分享區的資料夾名稱（要改名改這裡即可）
ZONE_READONLY = "read-only"
ZONE_APPEND = "read&append"
ZONE_TASK = "task"
ZONE_PERSONAL = "personal"

# 每個 zone 需要的「最低 tier」。peer 的 tier 要 >= 這個才看得到該區。
# common 看得到 read-only / read&append；task 多看 task/；personal 全看。
ZONE_MIN_TIER = {
    ZONE_READONLY: "common",
    ZONE_APPEND:   "common",
    ZONE_TASK:     "task",
    ZONE_PERSONAL: "personal",
}
ALL_ZONES = tuple(ZONE_MIN_TIER.keys())

# ask 的兩種模式（只在這裡宣告一次）：
#   remote = 「B 幫 A 統整」：B 的 AI 讀自己 share/ 生成答案回傳（B 要有模型，A 不用）
#   local  = 「A 自己讀資料」：B 只回傳 tier 過濾後的原始 chunks，A 用自己的 AI 生成（A 要有模型）
ASK_MODES = ("remote", "local")
DEFAULT_ASK_MODE = "remote"


def _tier_rank(tier: str) -> int:
    """tier 在 TIERS 裡的序（越高權限越大）；不合法退回 common。"""
    try:
        return TIERS.index(normalize_tier(tier))
    except ValueError:
        return TIERS.index(DEFAULT_TIER)


def _zones_for_tier(tier: str) -> set:
    """這個 tier 看得到哪些 zone 資料夾名稱。"""
    r = _tier_rank(tier)
    return {z for z, need in ZONE_MIN_TIER.items() if _tier_rank(need) <= r}


# ── 應用層 JSON schema（解密後的明文）──────────────────────
class FileRequest(BaseModel):
    id: str                              # 用來對應回應
    op: str                              # "read" | "append" | "list" | "ask" | "capability"
    path: str = ""                       # 相對 share/ 的路徑；list 可留空；ask/capability 不需要
    content: Optional[str] = None        # append 才需要
    query: Optional[str] = None          # ask 才需要：要問對方 AI 的自然語言問題
    topic: Optional[str] = None          # capability 才需要：要探測對方對哪個題目有資料
    mode: str = DEFAULT_ASK_MODE         # ask 模式：remote（B 統整）/ local（A 自己讀資料）


class FileResponse(BaseModel):
    id: str                              # 對應的 request id
    ok: bool                             # 是否成功
    content: Optional[str] = None        # read 成功時回傳整個檔案
    entries: Optional[List[str]] = None  # list 成功時回傳路徑清單（資料夾結尾帶 /）
    answer: Optional[str] = None         # ask remote 模式：B 的 AI 生成的純文字答覆
    context: Optional[List[str]] = None  # ask local 模式：B 回傳的原始 chunks（A 自己拿去生成）
    capability: Optional[Dict] = None    # capability 模式：{"relevant": bool, "topics": [...], "summary": str}
    error: Optional[str] = None          # path_denied / not_shared / permission_denied / not_found / bad_op / io_error / missing_topic / missing_query


# ── share/ 結構 ───────────────────────────────────────────
def ensure_share(share: str) -> None:
    """確保四個 zone 都存在（read-only / read&append / task / personal）。"""
    for zone in ALL_ZONES:
        os.makedirs(os.path.join(share, zone), exist_ok=True)


# ── 送訊方：組裝 REQUEST payload ───────────────────────────
def make_request(op: str, path: str = "", content: Optional[str] = None,
                 query: Optional[str] = None, topic: Optional[str] = None,
                 mode: str = DEFAULT_ASK_MODE) -> Dict:
    """組一個 REQUEST payload（自動產生 id）。
       read 不帶 content；list 可不帶 path；
       ask 用 query 帶問題、mode 選 remote/local；
       capability 用 topic 帶要探測的題目。"""
    req = FileRequest(
        id=uuid.uuid4().hex[:8],
        op=op,
        path=path or "",
        content=(content or "") if op == "append" else None,
        query=query if op == "ask" else None,
        topic=topic if op == "capability" else None,
        mode=(mode if mode in ASK_MODES else DEFAULT_ASK_MODE) if op == "ask" else DEFAULT_ASK_MODE,
    )
    return req.model_dump(exclude_none=True)


# ── 收訊方：權限 / 路徑檢查（read / append 用）────────────
def _safe_resolve(share: str, rel_path: str) -> Tuple[Optional[str], Optional[str]]:
    """把 rel_path 解析成絕對路徑，並確認仍在 share/ 內。回傳 (full, None) 或 (None, "path_denied")。"""
    share_root = os.path.realpath(share)
    full = os.path.realpath(os.path.join(share_root, rel_path))
    try:
        if os.path.commonpath([share_root, full]) != share_root:
            return None, "path_denied"
    except ValueError:
        return None, "path_denied"
    return full, None


def _check(req: FileRequest, share: str,
           tier: str = DEFAULT_TIER) -> Tuple[Optional[str], Optional[str]]:
    """read / append 的檢查（含 tier 權限）。回傳 (可操作的絕對路徑, None) 或 (None, 錯誤碼)。"""
    share_root = os.path.realpath(share)
    full, err = _safe_resolve(share, req.path)
    if err:
        return None, err

    # 必須落在某個 zone 內
    rel = os.path.relpath(full, share_root)
    top = rel.split(os.sep)[0]
    if top not in ALL_ZONES:
        return None, "not_shared"

    # tier 不夠 → 一律回 not_shared（不洩漏該區是否存在）
    if top not in _zones_for_tier(tier):
        return None, "not_shared"

    # append 只允許在「可追加」的區（目前只有 read&append/）
    if req.op == "append" and top != ZONE_APPEND:
        return None, "permission_denied"

    return full, None


# ── 收訊方：實際操作 ───────────────────────────────────────
def _do_file_op(req: FileRequest, full: str) -> FileResponse:
    """在已通過檢查的絕對路徑上執行 read / append。"""
    try:
        if req.op == "read":
            with open(full, encoding="utf-8") as f:
                return FileResponse(id=req.id, ok=True, content=f.read())
        elif req.op == "append":
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "a", encoding="utf-8") as f:
                f.write(req.content or "")
            return FileResponse(id=req.id, ok=True)
        else:
            return FileResponse(id=req.id, ok=False, error="bad_op")
    except FileNotFoundError:
        return FileResponse(id=req.id, ok=False, error="not_found")
    except IsADirectoryError:
        return FileResponse(id=req.id, ok=False, error="is_a_directory")
    except Exception as e:
        return FileResponse(id=req.id, ok=False, error=f"io_error: {e}")


def _do_list(req: FileRequest, share: str,
             tier: str = DEFAULT_TIER) -> FileResponse:
    """列出 share/（或其子目錄）的結構，只顯示這個 tier 看得到的 zone。"""
    share_root = os.path.realpath(share)
    full, err = _safe_resolve(share, req.path)
    if err:
        return FileResponse(id=req.id, ok=False, error=err)
    if not os.path.exists(full):
        return FileResponse(id=req.id, ok=False, error="not_found")

    allowed = _zones_for_tier(tier)

    # 列特定子目錄時：它的頂層 zone 必須是這個 tier 看得到的
    rel_root = os.path.relpath(full, share_root)
    if rel_root != ".":
        top = rel_root.split(os.sep)[0]
        if top not in allowed:
            return FileResponse(id=req.id, ok=False, error="not_shared")

    entries: List[str] = []
    if os.path.isdir(full):
        for root, dirs, files in os.walk(full):
            # 在 share 根目錄這層，把 tier 看不到的 zone 直接剪掉（不往下走）
            if os.path.realpath(root) == share_root:
                dirs[:] = [d for d in dirs if d in allowed]
            dirs.sort()
            for d in dirs:
                entries.append(os.path.relpath(os.path.join(root, d), share_root) + "/")
            for f in sorted(files):
                entries.append(os.path.relpath(os.path.join(root, f), share_root))
    else:  # 指到單一檔案
        entries.append(os.path.relpath(full, share_root))

    entries.sort()
    return FileResponse(id=req.id, ok=True, entries=entries)


# ── 收訊方：ask（讓本地 Ollama 回答對方的問題）─────────────
# 對方既然在白名單裡，預設讓 LLM 看到整個 share/ 作為 context（兩個 zone 都包含）。
# 注意：不是把整個 share/ 回傳給對方，只是給 LLM 當參考；最終只回 LLM 的 answer。
_ASK_CTX_LIMIT = 50_000   # 防止 context 過大爆炸（單位 bytes）

def _collect_ask_context(share: str, tier: str = DEFAULT_TIER) -> List[str]:
    """蒐集這個 tier 看得到的 zone 底下的文字檔，當 LLM 的 context chunks（簡易版 RAG）。"""
    chunks: List[str] = []
    total = 0
    share_root = os.path.realpath(share)
    if not os.path.isdir(share_root):
        return chunks
    # 只走這個 tier 被允許的 zone（tier 不夠的資料夾根本不進 LLM context）
    for zone in sorted(_zones_for_tier(tier)):
        zone_root = os.path.join(share_root, zone)
        zone_root_real = os.path.realpath(zone_root)
        if not os.path.isdir(zone_root_real):
            continue
        for root, _, files in os.walk(zone_root_real):
            for name in sorted(files):
                if name.startswith("."):
                    continue
                full = os.path.join(root, name)
                resolved = os.path.realpath(full)
                if os.path.commonpath([zone_root_real, resolved]) != zone_root_real:
                    continue
                rel = os.path.relpath(full, share_root)
                try:
                    with open(resolved, encoding="utf-8") as f:
                        body = f.read()
                except (UnicodeDecodeError, OSError):
                    continue   # 跳過非文字檔
                chunk = f"--- {rel} ---\n{body}"
                if total + len(chunk) > _ASK_CTX_LIMIT:
                    return chunks
                chunks.append(chunk)
                total += len(chunk)
    return chunks


async def _do_ask(req: FileRequest, share: str, owner: str, sender_pubkey: str,
                  tier: str, model: Optional[str] = None) -> FileResponse:
    if not req.query:
        return FileResponse(id=req.id, ok=False, error="missing_query")
    mode = req.mode if req.mode in ASK_MODES else DEFAULT_ASK_MODE
    ctx = _collect_ask_context(share, tier)

    # local 模式：B 不跑 AI，只把 tier 過濾後的原始 chunks 回給 A，由 A 自己生成。
    if mode == "local":
        print(f"   📤 [Ask/local] tier={tier} 回傳 {len(ctx)} 個原始 chunks 給 A 自己讀")
        return FileResponse(id=req.id, ok=True, context=ctx)

    # remote 模式（預設）：B 的 AI 讀自己 share/ 生成答案。
    print(f"   🧠 [Ask/remote] '{req.query[:60]}'… tier={tier} ctx_chunks={len(ctx)} model={model or 'auto'}")
    try:
        text = await ai_client.answer(
            owner=owner,
            peer_pubkey=sender_pubkey,
            tier=tier,
            query_text=req.query,
            ctx_chunks=ctx,
            model=model,
        )
    except Exception as e:
        return FileResponse(id=req.id, ok=False, error=f"ai_error: {e}")
    return FileResponse(id=req.id, ok=True, answer=text)


async def _do_capability(req: FileRequest, share: str, owner: str, sender_pubkey: str,
                           tier: str, model: Optional[str] = None) -> FileResponse:
    """收 capability 探測：用本機 AI 看 tier 內 share/ 自我評估「對 topic 有沒有資料／哪些面向」。
    回傳 capability={"relevant": bool, "topics": [...], "summary": str}。"""
    if not req.topic:
        return FileResponse(id=req.id, ok=False, error="missing_topic")
    ctx = _collect_ask_context(share, tier)
    print(f"   🔍 [Capability] topic={req.topic[:60]!r} tier={tier} ctx_chunks={len(ctx)}")
    try:
        cap = await ai_client.capability_probe(
            owner=owner, peer_pubkey=sender_pubkey, tier=tier,
            topic=req.topic, ctx_chunks=ctx, model=model,
        )
    except Exception as e:
        return FileResponse(id=req.id, ok=False, error=f"ai_error: {e}")
    return FileResponse(id=req.id, ok=True, capability=cap)


async def handle_request(payload: Dict, share: str, *,
                          owner: str = "Anonymous", sender_pubkey: str = "",
                          tier: str = DEFAULT_TIER,
                          model: Optional[str] = None) -> Optional[Dict]:
    """解析 REQUEST → 權限/路徑檢查 → 執行 → 回傳要送回去的 RESPONSE payload。
    解析失敗回傳 None（呼叫端就不用回任何東西）。"""
    try:
        req = FileRequest.model_validate(payload)
    except Exception as e:
        print(f"   ⚠️ 無效 request: {e}")
        return None

    try:
        tier = normalize_tier(tier)   # 大小寫正規化
    except ValueError:
        tier = DEFAULT_TIER           # 網路入口寬鬆：不合法的 tier 一律降為 common，不讓它 crash

    if req.op == "ask" and req.query:
        op_label = f"[{req.mode}] " + req.query[:40] + "…"
    elif req.op == "capability" and req.topic:
        op_label = f"topic={req.topic[:40]}"
    else:
        op_label = (req.path or "(share 根目錄)")
    print(f"   📂 [Request] id={req.id} op={req.op} tier={tier} {op_label}")

    if req.op == "list":
        resp = _do_list(req, share, tier)
    elif req.op == "ask":
        resp = await _do_ask(req, share, owner=owner, sender_pubkey=sender_pubkey,
                              tier=tier, model=model)
    elif req.op == "capability":
        resp = await _do_capability(req, share, owner=owner, sender_pubkey=sender_pubkey,
                                     tier=tier, model=model)
    else:
        full, err = _check(req, share, tier)
        resp = FileResponse(id=req.id, ok=False, error=err) if err else _do_file_op(req, full)

    if resp.ok:
        if resp.entries is not None:
            extra = f" ({len(resp.entries)} 項)"
        elif resp.answer is not None:
            extra = " (AI 回應)"
        elif resp.capability is not None:
            rel = resp.capability.get("relevant")
            topics = resp.capability.get("topics") or []
            extra = f" (capability: relevant={rel}, topics={topics})"
        else:
            extra = ""
        print(f"   ↩️  [Response] id={resp.id} ok=True{extra}")
    else:
        print(f"   ⛔ [Denied] id={resp.id} {resp.error}")
    return resp.model_dump(exclude_none=True)


# ── 送訊方：顯示收到的 RESPONSE ────────────────────────────
def handle_response(payload: Dict) -> None:
    """解析並顯示 RESPONSE。"""
    try:
        resp = FileResponse.model_validate(payload)
    except Exception as e:
        print(f"   ⚠️ 無效 response: {e}")
        return

    if not resp.ok:
        print(f"   ❌ [Reply id={resp.id}] 失敗：{resp.error}")
    elif resp.capability is not None:
        cap = resp.capability
        rel = "✅ 有相關" if cap.get("relevant") else "❌ 無相關"
        topics = cap.get("topics") or []
        summ = cap.get("summary", "")
        print(f"   🔍 [Reply id={resp.id}] capability：{rel}")
        if topics:
            print(f"      面向：{', '.join(topics)}")
        if summ:
            print(f"      摘要：{summ}")
    elif resp.entries is not None:
        print(f"   📁 [Reply id={resp.id}] list 成功（{len(resp.entries)} 項）：")
        if not resp.entries:
            print("   （空的）")
        for e in resp.entries:
            depth = e.rstrip("/").count("/")
            name = e.rstrip("/").split("/")[-1] + ("/" if e.endswith("/") else "")
            icon = "📂" if e.endswith("/") else "📄"
            print("   " + "    " * depth + f"{icon} {name}")
    elif resp.content is not None:
        print(f"   ✅ [Reply id={resp.id}] read 成功，檔案內容：")
        print("   ┌────────────────────────────")
        for line in (resp.content.splitlines() or [""]):
            print(f"   │ {line}")
        print("   └────────────────────────────")
    elif resp.answer is not None:
        print(f"   🤖 [Reply id={resp.id}] AI 回應：")
        print("   ┌────────────────────────────")
        for line in (resp.answer.splitlines() or [""]):
            print(f"   │ {line}")
        print("   └────────────────────────────")
    else:
        print(f"   ✅ [Reply id={resp.id}] append 成功")


# ── 自我測試 ──────────────────────────────────────────────
if __name__ == "__main__":
    import asyncio
    import tempfile

    import agents as _agents

    share = tempfile.mkdtemp()
    ensure_share(share)
    ro = f"{ZONE_READONLY}/doc.md"
    rw = f"{ZONE_APPEND}/log.md"
    tk = f"{ZONE_TASK}/plan.md"
    pv = f"{ZONE_PERSONAL}/secret.md"
    with open(os.path.join(share, ro), "w", encoding="utf-8") as f:
        f.write("read only 內容\n")
    with open(os.path.join(share, tk), "w", encoding="utf-8") as f:
        f.write("task 內容\n")
    with open(os.path.join(share, pv), "w", encoding="utf-8") as f:
        f.write("personal 機密\n")

    def call(payload, tier=DEFAULT_TIER):
        return asyncio.run(handle_request(payload, share, tier=tier))

    # read / append / 權限
    assert call(make_request("read", ro))["ok"] is True
    assert call(make_request("append", ro, "x"))["error"] == "permission_denied"
    assert call(make_request("append", rw, "一行\n"))["ok"] is True
    assert call(make_request("read", rw))["content"] == "一行\n"
    assert call(make_request("read", "secret.md"))["error"] == "not_shared"
    assert call(make_request("read", "../../etc/passwd"))["error"] == "path_denied"
    assert call(make_request("read", f"{ZONE_READONLY}/nope.md"))["error"] == "not_found"

    # list：整個 share（應看到兩個 zone 與其中的檔）
    r = call(make_request("list"))
    assert r["ok"] is True
    assert f"{ZONE_READONLY}/" in r["entries"] and ro in r["entries"]
    assert f"{ZONE_APPEND}/" in r["entries"] and rw in r["entries"]
    handle_response(r)

    # list：指定子目錄
    r2 = call(make_request("list", ZONE_READONLY))
    assert r2["ok"] is True and ro in r2["entries"]

    # list：路徑逃逸 → path_denied
    assert call(make_request("list", "../.."))["error"] == "path_denied"

    # 不合法 payload → None
    assert call({"oops": 1}) is None

    # ask 缺 query → missing_query
    bad_ask = make_request("ask")
    assert call(bad_ask)["error"] == "missing_query"

    # ── tier 權限 ───────────────────────────────────────────
    # common（預設）看不到 task / personal
    assert call(make_request("read", tk))["error"] == "not_shared"
    assert call(make_request("read", pv))["error"] == "not_shared"
    rc = call(make_request("list"))
    assert f"{ZONE_TASK}/" not in rc["entries"] and f"{ZONE_PERSONAL}/" not in rc["entries"]
    assert call(make_request("list", ZONE_TASK))["error"] == "not_shared"

    # task 看得到 task，但看不到 personal
    assert call(make_request("read", tk), tier="task")["content"] == "task 內容\n"
    assert call(make_request("read", pv), tier="task")["error"] == "not_shared"
    rt = call(make_request("list"), tier="task")
    assert f"{ZONE_TASK}/" in rt["entries"] and f"{ZONE_PERSONAL}/" not in rt["entries"]

    # personal 全看得到
    assert call(make_request("read", pv), tier="personal")["content"] == "personal 機密\n"
    rp = call(make_request("list"), tier="personal")
    assert f"{ZONE_PERSONAL}/" in rp["entries"] and f"{ZONE_TASK}/" in rp["entries"]

    # tier 大小寫 / 不合法 → 寬鬆處理（不 crash）
    assert call(make_request("read", tk), tier="TASK")["content"] == "task 內容\n"
    assert call(make_request("read", tk), tier="bogus")["error"] == "not_shared"  # 降為 common

    # _collect_ask_context 按 tier 過濾：層級越高，看到的 chunks 越多（或相等）
    assert len(_collect_ask_context(share, "common")) < len(_collect_ask_context(share, "personal"))

    # 向下相容：舊的 agent meta（沒有 tier 欄位）→ 視為 common
    assert _agents.get_tier({"name": "Old", "added": "2026-01-01"}) == "common"
    assert _agents.get_tier({"name": "Bad", "tier": "nonsense"}) == "common"

    # ── ask 雙模式 ──────────────────────────────────────────
    # local 模式：B 不跑 AI，回 context（原始 chunks）而非 answer
    # 此時 share 內有：read-only/doc.md、read&append/log.md（前面 append 建的）、task/plan.md、personal/secret.md
    rl = call(make_request("ask", query="任務?", mode="local"), tier="task")
    assert rl["ok"] is True and "answer" not in rl
    assert isinstance(rl["context"], list)
    rl_common = call(make_request("ask", query="任務?", mode="local"), tier="common")
    # 層級越高，local 回的 chunks 應該越多（tier 也吃在 context 上）
    assert len(rl_common["context"]) < len(rl["context"])
    # local 的 context 也吃 tier：personal 檔不會出現在 task 的 chunks 裡
    assert all("personal 機密" not in c for c in rl["context"])
    assert any("task 內容" in c for c in rl["context"])                  # task 看得到 task 檔
    assert all("task 內容" not in c for c in rl_common["context"])        # common 看不到

    # make_request 預設模式 = remote；沒給 mode 的舊 payload 也視為 remote
    assert make_request("ask", query="x")["mode"] == "remote"
    legacy = {"id": "abcd1234", "op": "ask", "query": "x"}               # 沒有 mode 欄位
    assert FileRequest.model_validate(legacy).mode == "remote"
    # 不合法 mode → 退回 remote
    assert make_request("ask", query="x", mode="bogus")["mode"] == "remote"

    print("✅ app_layer self-test passed（read/append/list/ask + tier ACL + ask 雙模式 + 向下相容 + 路徑安全）")
    print("ℹ️  ask remote/local 的完整 AI 測試（需 ollama）：python3 ai_client.py")
