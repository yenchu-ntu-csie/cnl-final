"""
LinkedOut 應用層 (L3)：解析「解密後的 JSON」、執行檔案 read / append、組裝回應。

與網路層 (p2p_node) 解耦 —— 這裡只處理明文 payload (dict) 的進出，
完全不碰加密、relay、socket。流程：
    p2p_node 解密封包 → 把明文 payload 丟進這裡 → 拿回要回傳的 payload（再由 p2p_node 加密送出）

封包外層 type 對應：
    "REQUEST"  → handle_request()   解析 → 執行檔案操作 → 回傳 RESPONSE payload
    "RESPONSE" → handle_response()  解析 → 顯示結果

注意：路徑安全 / 權限之後由另一個白名單層處理，這裡先專注 JSON 的解析與檔案操作。
"""

import os
import uuid
from typing import Dict, Optional

from pydantic import BaseModel


# ── 應用層 JSON schema（解密後的明文）──────────────────────
class FileRequest(BaseModel):
    id: str                              # 用來對應回應
    op: str                              # "read" | "append"
    path: str                            # 要操作的檔案
    content: Optional[str] = None        # append 才需要


class FileResponse(BaseModel):
    id: str                              # 對應的 request id
    ok: bool                             # 是否成功
    content: Optional[str] = None        # read 成功時回傳整個檔案
    error: Optional[str] = None          # 失敗原因（not_found / bad_op / io_error）


# ── 送訊方：組裝 REQUEST payload ───────────────────────────
def make_request(op: str, path: str, content: Optional[str] = None) -> Dict:
    """組一個 REQUEST payload（自動產生 id）。read 不帶 content。"""
    req = FileRequest(
        id=uuid.uuid4().hex[:8],
        op=op,
        path=path,
        content=(content or "") if op == "append" else None,
    )
    return req.model_dump(exclude_none=True)


# ── 收訊方：執行檔案操作 ───────────────────────────────────
def _do_file_op(req: FileRequest, vault: str) -> FileResponse:
    """在 vault 目錄內執行 read / append（路徑安全與權限之後另外做）。"""
    full = os.path.join(vault, req.path)
    try:
        if req.op == "read":
            with open(full, encoding="utf-8") as f:
                return FileResponse(id=req.id, ok=True, content=f.read())
        elif req.op == "append":
            os.makedirs(os.path.dirname(full) or vault, exist_ok=True)
            with open(full, "a", encoding="utf-8") as f:
                f.write(req.content or "")
            return FileResponse(id=req.id, ok=True)
        else:
            return FileResponse(id=req.id, ok=False, error="bad_op")
    except FileNotFoundError:
        return FileResponse(id=req.id, ok=False, error="not_found")
    except Exception as e:
        return FileResponse(id=req.id, ok=False, error=f"io_error: {e}")


def handle_request(payload: Dict, vault: str) -> Optional[Dict]:
    """解析 REQUEST → 執行檔案操作 → 回傳要送回去的 RESPONSE payload。
    解析失敗回傳 None（呼叫端就不用回任何東西）。"""
    try:
        req = FileRequest.model_validate(payload)
    except Exception as e:
        print(f"   ⚠️ 無效 request: {e}")
        return None

    print(f"   📂 [Request] id={req.id} op={req.op} path={req.path}")
    resp = _do_file_op(req, vault)
    print(f"   ↩️  [Response] id={resp.id} ok={resp.ok}"
          + (f" error={resp.error}" if resp.error else ""))
    return resp.model_dump()


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
    elif resp.content is not None:
        print(f"   ✅ [Reply id={resp.id}] read 成功，檔案內容：")
        print("   ┌────────────────────────────")
        for line in (resp.content.splitlines() or [""]):
            print(f"   │ {line}")
        print("   └────────────────────────────")
    else:
        print(f"   ✅ [Reply id={resp.id}] append 成功")


# ── 自我測試 ──────────────────────────────────────────────
if __name__ == "__main__":
    import tempfile

    d = tempfile.mkdtemp()
    # append → read round-trip（純應用層，不經網路）
    req = make_request("append", "notes.md", "第一行\n")
    resp = handle_request(req, d)
    assert resp["ok"] is True

    req2 = make_request("read", "notes.md")
    resp2 = handle_request(req2, d)
    assert resp2["ok"] is True and resp2["content"] == "第一行\n"
    handle_response(resp2)

    # 讀不存在的檔 → not_found
    resp3 = handle_request(make_request("read", "nope.md"), d)
    assert resp3["ok"] is False and resp3["error"] == "not_found"
    handle_response(resp3)

    # 不合法 op
    assert handle_request({"id": "x", "op": "delete", "path": "a"}, d)["error"] == "bad_op"
    # 不合法 payload（缺欄位）→ None
    assert handle_request({"oops": 1}, d) is None
    print("✅ app_layer self-test passed")
