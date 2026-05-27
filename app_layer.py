"""
LinkedOut 應用層 (L3)：解析「解密後的 JSON」、在 share/ 內執行檔案操作、組裝回應。

與網路層 (p2p_node) 解耦 —— 這裡只處理明文 payload (dict) 的進出，
完全不碰加密、relay、socket。流程：
    p2p_node 解密封包 → 把明文 payload 丟進來 → 拿回要回傳的 payload（再由 p2p_node 加密送出）

封包外層 type 對應：
    "REQUEST"  → handle_request()   檢查權限/路徑 → 執行 read/append → 回傳 RESPONSE payload
    "RESPONSE" → handle_response()  解析 → 顯示結果

── 檔案分享規則 ──────────────────────────────────────────
雙方本地都有一個 share/ 資料夾，底下分兩區（zone）：
    share/read-only/      對方只能 read，不能 append
    share/read&append/    對方可以 read，也可以 append
規則檢查（在實際動檔案前）：
    1. 路徑安全：解析後必須仍在 share/ 內（擋 ../ 與絕對路徑逃逸）→ 否則 path_denied
    2. 必須落在某個 zone 內（read-only / read&append）→ 否則 not_shared
    3. append 只允許在 read&append/ → 否則 permission_denied
"""

import os
import uuid
from typing import Dict, Optional, Tuple

from pydantic import BaseModel

# 兩個分享區的資料夾名稱（要改名改這裡即可）
ZONE_READONLY = "read-only"
ZONE_APPEND = "read&append"


# ── 應用層 JSON schema（解密後的明文）──────────────────────
class FileRequest(BaseModel):
    id: str                              # 用來對應回應
    op: str                              # "read" | "append"
    path: str                            # 相對 share/ 的路徑，含 zone，如 read-only/notes.md
    content: Optional[str] = None        # append 才需要


class FileResponse(BaseModel):
    id: str                              # 對應的 request id
    ok: bool                             # 是否成功
    content: Optional[str] = None        # read 成功時回傳整個檔案
    error: Optional[str] = None          # path_denied / not_shared / permission_denied / not_found / bad_op / io_error


# ── share/ 結構 ───────────────────────────────────────────
def ensure_share(share: str) -> None:
    """確保 share/read-only 與 share/read&append 兩個 zone 存在。"""
    os.makedirs(os.path.join(share, ZONE_READONLY), exist_ok=True)
    os.makedirs(os.path.join(share, ZONE_APPEND), exist_ok=True)


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


# ── 收訊方：權限 / 路徑檢查 ────────────────────────────────
def _check(req: FileRequest, share: str) -> Tuple[Optional[str], Optional[str]]:
    """檢查 request 是否合法。回傳 (可操作的絕對路徑, None) 或 (None, 錯誤碼)。"""
    share_root = os.path.realpath(share)
    full = os.path.realpath(os.path.join(share_root, req.path))

    # 1. 路徑安全：解析後必須仍在 share/ 內（擋 ../、絕對路徑、symlink 逃逸）
    try:
        if os.path.commonpath([share_root, full]) != share_root:
            return None, "path_denied"
    except ValueError:
        return None, "path_denied"

    # 2. 必須落在某個 zone 內
    rel = os.path.relpath(full, share_root)
    top = rel.split(os.sep)[0]
    if top == ZONE_READONLY:
        zone = "ro"
    elif top == ZONE_APPEND:
        zone = "rw"
    else:
        return None, "not_shared"

    # 3. append 只允許在 read&append/
    if req.op == "append" and zone == "ro":
        return None, "permission_denied"

    return full, None


# ── 收訊方：實際檔案操作 ───────────────────────────────────
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


def handle_request(payload: Dict, share: str) -> Optional[Dict]:
    """解析 REQUEST → 權限/路徑檢查 → 執行 → 回傳要送回去的 RESPONSE payload。
    解析失敗回傳 None（呼叫端就不用回任何東西）。"""
    try:
        req = FileRequest.model_validate(payload)
    except Exception as e:
        print(f"   ⚠️ 無效 request: {e}")
        return None

    print(f"   📂 [Request] id={req.id} op={req.op} path={req.path}")
    full, err = _check(req, share)
    if err:
        resp = FileResponse(id=req.id, ok=False, error=err)
        print(f"   ⛔ [Denied] id={req.id} {err}")
    else:
        resp = _do_file_op(req, full)
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

    share = tempfile.mkdtemp()
    ensure_share(share)
    ro = f"{ZONE_READONLY}/doc.md"
    rw = f"{ZONE_APPEND}/log.md"

    # 先在 read-only 放一個檔（模擬本機擁有者放的）
    with open(os.path.join(share, ro), "w", encoding="utf-8") as f:
        f.write("read only 內容\n")

    # read-only：可讀
    assert handle_request(make_request("read", ro), share)["ok"] is True
    # read-only：不可 append → permission_denied
    assert handle_request(make_request("append", ro, "x"), share)["error"] == "permission_denied"
    # read&append：可 append、可讀
    assert handle_request(make_request("append", rw, "一行\n"), share)["ok"] is True
    assert handle_request(make_request("read", rw), share)["content"] == "一行\n"
    # 不在任何 zone → not_shared
    assert handle_request(make_request("read", "secret.md"), share)["error"] == "not_shared"
    # 路徑逃逸 → path_denied
    assert handle_request(make_request("read", "../../etc/passwd"), share)["error"] == "path_denied"
    assert handle_request(make_request("read", "/etc/passwd"), share)["error"] == "path_denied"
    # read-only 讀不存在的檔 → not_found
    assert handle_request(make_request("read", f"{ZONE_READONLY}/nope.md"), share)["error"] == "not_found"
    # 不合法 payload → None
    assert handle_request({"oops": 1}, share) is None

    print("✅ app_layer self-test passed（含 zone 權限 + 路徑安全）")
