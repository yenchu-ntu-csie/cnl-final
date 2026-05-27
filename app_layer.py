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

# 兩個分享區的資料夾名稱（要改名改這裡即可）
ZONE_READONLY = "read-only"
ZONE_APPEND = "read&append"


# ── 應用層 JSON schema（解密後的明文）──────────────────────
class FileRequest(BaseModel):
    id: str                              # 用來對應回應
    op: str                              # "read" | "append" | "list"
    path: str = ""                       # 相對 share/ 的路徑；list 可留空表示整個 share
    content: Optional[str] = None        # append 才需要


class FileResponse(BaseModel):
    id: str                              # 對應的 request id
    ok: bool                             # 是否成功
    content: Optional[str] = None        # read 成功時回傳整個檔案
    entries: Optional[List[str]] = None  # list 成功時回傳路徑清單（資料夾結尾帶 /）
    error: Optional[str] = None          # path_denied / not_shared / permission_denied / not_found / bad_op / io_error


# ── share/ 結構 ───────────────────────────────────────────
def ensure_share(share: str) -> None:
    """確保 share/read-only 與 share/read&append 兩個 zone 存在。"""
    os.makedirs(os.path.join(share, ZONE_READONLY), exist_ok=True)
    os.makedirs(os.path.join(share, ZONE_APPEND), exist_ok=True)


# ── 送訊方：組裝 REQUEST payload ───────────────────────────
def make_request(op: str, path: str = "", content: Optional[str] = None) -> Dict:
    """組一個 REQUEST payload（自動產生 id）。read 不帶 content；list 可不帶 path。"""
    req = FileRequest(
        id=uuid.uuid4().hex[:8],
        op=op,
        path=path or "",
        content=(content or "") if op == "append" else None,
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


def _check(req: FileRequest, share: str) -> Tuple[Optional[str], Optional[str]]:
    """read / append 的檢查。回傳 (可操作的絕對路徑, None) 或 (None, 錯誤碼)。"""
    share_root = os.path.realpath(share)
    full, err = _safe_resolve(share, req.path)
    if err:
        return None, err

    # 必須落在某個 zone 內
    rel = os.path.relpath(full, share_root)
    top = rel.split(os.sep)[0]
    if top == ZONE_READONLY:
        zone = "ro"
    elif top == ZONE_APPEND:
        zone = "rw"
    else:
        return None, "not_shared"

    # append 只允許在 read&append/
    if req.op == "append" and zone == "ro":
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


def _do_list(req: FileRequest, share: str) -> FileResponse:
    """列出 share/（或其子目錄）的資料夾結構。只做路徑安全，不限 zone。"""
    share_root = os.path.realpath(share)
    full, err = _safe_resolve(share, req.path)
    if err:
        return FileResponse(id=req.id, ok=False, error=err)
    if not os.path.exists(full):
        return FileResponse(id=req.id, ok=False, error="not_found")

    entries: List[str] = []
    if os.path.isdir(full):
        for root, dirs, files in os.walk(full):
            dirs.sort()
            for d in dirs:
                entries.append(os.path.relpath(os.path.join(root, d), share_root) + "/")
            for f in sorted(files):
                entries.append(os.path.relpath(os.path.join(root, f), share_root))
    else:  # 指到單一檔案
        entries.append(os.path.relpath(full, share_root))

    entries.sort()
    return FileResponse(id=req.id, ok=True, entries=entries)


def handle_request(payload: Dict, share: str) -> Optional[Dict]:
    """解析 REQUEST → 權限/路徑檢查 → 執行 → 回傳要送回去的 RESPONSE payload。
    解析失敗回傳 None（呼叫端就不用回任何東西）。"""
    try:
        req = FileRequest.model_validate(payload)
    except Exception as e:
        print(f"   ⚠️ 無效 request: {e}")
        return None

    print(f"   📂 [Request] id={req.id} op={req.op} path={req.path or '(share 根目錄)'}")

    if req.op == "list":
        resp = _do_list(req, share)
    else:
        full, err = _check(req, share)
        resp = FileResponse(id=req.id, ok=False, error=err) if err else _do_file_op(req, full)

    if resp.ok:
        extra = f" ({len(resp.entries)} 項)" if resp.entries is not None else ""
        print(f"   ↩️  [Response] id={resp.id} ok=True{extra}")
    else:
        print(f"   ⛔ [Denied] id={resp.id} {resp.error}")
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
    else:
        print(f"   ✅ [Reply id={resp.id}] append 成功")


# ── 自我測試 ──────────────────────────────────────────────
if __name__ == "__main__":
    import tempfile

    share = tempfile.mkdtemp()
    ensure_share(share)
    ro = f"{ZONE_READONLY}/doc.md"
    rw = f"{ZONE_APPEND}/log.md"
    with open(os.path.join(share, ro), "w", encoding="utf-8") as f:
        f.write("read only 內容\n")

    # read / append / 權限
    assert handle_request(make_request("read", ro), share)["ok"] is True
    assert handle_request(make_request("append", ro, "x"), share)["error"] == "permission_denied"
    assert handle_request(make_request("append", rw, "一行\n"), share)["ok"] is True
    assert handle_request(make_request("read", rw), share)["content"] == "一行\n"
    assert handle_request(make_request("read", "secret.md"), share)["error"] == "not_shared"
    assert handle_request(make_request("read", "../../etc/passwd"), share)["error"] == "path_denied"
    assert handle_request(make_request("read", f"{ZONE_READONLY}/nope.md"), share)["error"] == "not_found"

    # list：整個 share（應看到兩個 zone 與其中的檔）
    r = handle_request(make_request("list"), share)
    assert r["ok"] is True
    assert f"{ZONE_READONLY}/" in r["entries"] and ro in r["entries"]
    assert f"{ZONE_APPEND}/" in r["entries"] and rw in r["entries"]
    handle_response(r)

    # list：指定子目錄
    r2 = handle_request(make_request("list", ZONE_READONLY), share)
    assert r2["ok"] is True and ro in r2["entries"]

    # list：路徑逃逸 → path_denied
    assert handle_request(make_request("list", "../.."), share)["error"] == "path_denied"

    # 不合法 payload → None
    assert handle_request({"oops": 1}, share) is None

    print("✅ app_layer self-test passed（read/append/list + zone 權限 + 路徑安全）")
