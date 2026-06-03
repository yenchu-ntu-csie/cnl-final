"""
LinkedOut E2EE — 端對端加密層

設計（對應 proposal 的 Diffie-Hellman + 臨時金鑰 + Public Key 信任白名單）：
  - 每個節點持有一把長期 X25519 靜態金鑰，其 public key (hex) 就是節點身分。
  - 每則訊息採 Noise「X」單向模式：寄件者額外產生一把「臨時 (ephemeral)」金鑰，
    與收件者做兩次 Diffie-Hellman：
        es = DH(ephemeral_priv, recipient_static_pub)     → 每則訊息獨立的臨時性
        ss = DH(sender_static_priv, recipient_static_pub) → 寄件者身分驗證
    兩段共享秘密經 HKDF-SHA256 導出對稱金鑰，再以 ChaCha20-Poly1305 (AEAD) 加密。
  - 封包的路由 metadata (sender/target/msg_id/type) 綁進 AEAD 的 AAD，
    relay server 若竄改 metadata 會導致解密失敗。

提供的保證：
  - 機密性 + 完整性（AEAD）；relay 只看得到密文與路由位址。
  - 寄件者身分驗證：收件者能確認訊息確實來自宣稱的寄件者。
  - 每則訊息用一把臨時金鑰 → 對「寄件者長期金鑰外洩」有 forward secrecy。

⚠️ 不提供的保證（誠實說明，別寫過頭）：
  - 兩段 DH 都用到「收件者的長期靜態金鑰」。若該金鑰日後外洩，
    由於 eph_pub 是公開的，es=DH(eph_pub, recipient_static) 可被重算 → 過去錄下的密文可被解。
  - 即：對「收件者長期金鑰外洩」沒有 forward secrecy（Noise「X」單向模式的固有限制；
    真正的雙向 FS 需收件者也貢獻一把臨時金鑰，屬互動式握手，本實作未做）。
"""

import os
from typing import Dict

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

ALG = "x25519-noise-x-chacha20poly1305"
_HKDF_PREFIX = b"LinkedOut-v1|"


# ── 金鑰管理 ──────────────────────────────────────────────
def load_or_create_identity(path: str) -> X25519PrivateKey:
    """從檔案載入長期 X25519 私鑰；不存在則產生並存檔（權限 0600）。"""
    if os.path.exists(path):
        with open(path, "rb") as f:
            return X25519PrivateKey.from_private_bytes(f.read())

    priv = X25519PrivateKey.generate()
    raw = priv.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    with open(path, "wb") as f:
        f.write(raw)
    os.chmod(path, 0o600)
    return priv


def public_hex(priv: X25519PrivateKey) -> str:
    """取得對應的 public key（hex 字串），即節點的對外身分。"""
    return priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()


def _raw_pub(priv: X25519PrivateKey) -> bytes:
    return priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def _derive_key(dh_material: bytes, eph_pub: bytes, sender_pub: bytes, recipient_pub: bytes) -> bytes:
    """HKDF-SHA256，將兩把公鑰與臨時公鑰綁進 info 做 channel binding。"""
    info = _HKDF_PREFIX + eph_pub + sender_pub + recipient_pub
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=info).derive(dh_material)


# ── 加 / 解密 ─────────────────────────────────────────────
def encrypt(sender_priv: X25519PrivateKey, recipient_pub_hex: str,
            plaintext: bytes, aad: bytes = b"") -> Dict:
    """以收件者公鑰加密 plaintext，回傳可放進封包 payload 的 envelope dict。"""
    recipient_pub_raw = bytes.fromhex(recipient_pub_hex)
    recipient_pub = X25519PublicKey.from_public_bytes(recipient_pub_raw)

    eph = X25519PrivateKey.generate()
    eph_pub_raw = _raw_pub(eph)
    sender_pub_raw = _raw_pub(sender_priv)

    es = eph.exchange(recipient_pub)            # 臨時性：對「寄件者金鑰外洩」有 FS（注意：兩段都用收件者靜態鑰，對收件者金鑰外洩無 FS）
    ss = sender_priv.exchange(recipient_pub)    # sender authentication
    key = _derive_key(es + ss, eph_pub_raw, sender_pub_raw, recipient_pub_raw)

    nonce = os.urandom(12)
    ct = ChaCha20Poly1305(key).encrypt(nonce, plaintext, aad)
    return {
        "enc": True,
        "alg": ALG,
        "eph_pub": eph_pub_raw.hex(),
        "nonce": nonce.hex(),
        "ciphertext": ct.hex(),
    }


def decrypt(recipient_priv: X25519PrivateKey, sender_pub_hex: str,
            env: Dict, aad: bytes = b"") -> bytes:
    """以自身私鑰 + 宣稱的寄件者公鑰解密；驗證失敗（含寄件者偽造）會丟出例外。"""
    if env.get("alg") != ALG:
        raise ValueError(f"unsupported alg: {env.get('alg')}")

    eph_pub_raw = bytes.fromhex(env["eph_pub"])
    eph_pub = X25519PublicKey.from_public_bytes(eph_pub_raw)
    sender_pub_raw = bytes.fromhex(sender_pub_hex)
    sender_pub = X25519PublicKey.from_public_bytes(sender_pub_raw)
    recipient_pub_raw = _raw_pub(recipient_priv)

    es = recipient_priv.exchange(eph_pub)
    ss = recipient_priv.exchange(sender_pub)
    key = _derive_key(es + ss, eph_pub_raw, sender_pub_raw, recipient_pub_raw)

    nonce = bytes.fromhex(env["nonce"])
    return ChaCha20Poly1305(key).decrypt(nonce, bytes.fromhex(env["ciphertext"]), aad)


# ── 自我測試 ──────────────────────────────────────────────
if __name__ == "__main__":
    import tempfile

    d = tempfile.mkdtemp()
    a = load_or_create_identity(os.path.join(d, "a.key"))
    b = load_or_create_identity(os.path.join(d, "b.key"))
    a_pub, b_pub = public_hex(a), public_hex(b)

    aad = b"0xA|0xB|msg-1|QUERY"
    env = encrypt(a, b_pub, b'{"query_text": "Hi from A"}', aad)
    assert env["enc"] and env["alg"] == ALG

    # 正常解密
    pt = decrypt(b, a_pub, env, aad)
    assert pt == b'{"query_text": "Hi from A"}'
    print("✅ round-trip ok:", pt.decode())

    # 竄改 metadata → 解密失敗
    try:
        decrypt(b, a_pub, env, b"0xA|0xEVIL|msg-1|QUERY")
        raise SystemExit("❌ AAD tampering not detected")
    except Exception:
        print("✅ AAD tampering rejected")

    # 偽造寄件者身分 → 解密失敗
    c = load_or_create_identity(os.path.join(d, "c.key"))
    try:
        decrypt(b, public_hex(c), env, aad)
        raise SystemExit("❌ sender forgery not detected")
    except Exception:
        print("✅ sender forgery rejected")

    # 持久化：重新載入應得到相同公鑰
    assert public_hex(load_or_create_identity(os.path.join(d, "a.key"))) == a_pub
    print("✅ key persistence ok")
    print("🔑 demo pubkeys:", a_pub[:16], b_pub[:16])
