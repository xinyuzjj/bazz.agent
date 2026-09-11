"""敏感设置加密层（Windows DPAPI，零外部依赖——仅标准库 ctypes 调 crypt32.dll）

对落库 SQLite(state.db) 的敏感 settings 值（LLM api_key / 交易所 API Secret /
MCP token 等，见 state.SENSITIVE_SETTING_KEYS）做用户级 DPAPI 加密：密文与当前
Windows 账户绑定，state.db 被拷走也无法在别处解出，防止密钥明文落盘泄露。

存储格式约定：
- encrypt_secret(plain) -> "dpapi:" + base64(ciphertext)（DPAPI 用户级加密）
  非 Windows / DPAPI 调用失败 -> "plain:" + 原文（安全降级，功能与明文时代一致）
- decrypt_secret(stored)："dpapi:" 开头 -> DPAPI 解密（失败返回 ""，宁缺勿错）；
  "plain:" 开头 -> 去前缀还原；其余原样返回（兼容旧明文数据）。
- is_encrypted(stored)：是否为本模块产出的存储格式（dpapi:/plain: 前缀）。

同名共存说明：desktop_app 启动时把 src/ 插到 sys.path[0]，flat 布局下本文件会
遮蔽标准库 secrets；mcp_client.py `import secrets` 用 token_urlsafe 生成 PKCE
随机串。因此这里显式按路径加载标准库 secrets 并转发其公开 API（加载失败如
PyInstaller 冻结环境则用 os.urandom 提供等价实现），保证撞名不破坏既有功能。
"""
import base64
import ctypes
import os

_PREFIX_ENC = "dpapi:"
_PREFIX_PLAIN = "plain:"
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


# ---------------- 标准库 secrets 同名兼容转发（flat 布局遮蔽 stdlib 时兜底） ----------------

def _load_stdlib_secrets():
    """按路径显式加载标准库 secrets 并返回模块（本文件遮蔽 stdlib 时仍可用其 API）。"""
    try:
        import importlib.util
        path = os.path.join(os.path.dirname(os.__file__), "secrets.py")
        spec = importlib.util.spec_from_file_location("_stdlib_secrets", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


_stdlib = _load_stdlib_secrets()

if _stdlib is not None:
    choice = _stdlib.choice
    randbelow = _stdlib.randbelow
    randbits = _stdlib.randbits
    SystemRandom = _stdlib.SystemRandom
    token_bytes = _stdlib.token_bytes
    token_hex = _stdlib.token_hex
    token_urlsafe = _stdlib.token_urlsafe
    compare_digest = _stdlib.compare_digest
else:
    # 兜底（如 PyInstaller 冻结环境找不到 stdlib 源文件）：os.urandom 等价实现
    def token_bytes(nbytes=None):
        return os.urandom(32 if nbytes is None else nbytes)

    def token_hex(nbytes=None):
        return token_bytes(nbytes).hex()

    def token_urlsafe(nbytes=None):
        return base64.urlsafe_b64encode(token_bytes(nbytes)).rstrip(b"=").decode("ascii")

    def compare_digest(a, b):
        import hmac
        return hmac.compare_digest(a, b)


# ---------------- DPAPI（crypt32.dll，标准库 ctypes） ----------------

class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_ulong),
                ("pbData", ctypes.c_void_p)]


def _load_crypt32():
    """Windows 下加载 crypt32/kernel32 并声明签名；非 Windows 或失败返回 None（调用方降级）。"""
    if os.name != "nt":
        return None
    try:
        crypt32 = ctypes.WinDLL("crypt32")
        kernel32 = ctypes.WinDLL("kernel32")
        crypt32.CryptProtectData.argtypes = [
            ctypes.POINTER(_DATA_BLOB), ctypes.c_wchar_p, ctypes.c_void_p,
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(_DATA_BLOB)]
        crypt32.CryptProtectData.restype = ctypes.c_int
        crypt32.CryptUnprotectData.argtypes = [
            ctypes.POINTER(_DATA_BLOB), ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(_DATA_BLOB)]
        crypt32.CryptUnprotectData.restype = ctypes.c_int
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        return (crypt32, kernel32)
    except Exception:
        return None


_CRYPT = _load_crypt32()


def _blob_of(data: bytes) -> _DATA_BLOB:
    buf = ctypes.create_string_buffer(data, len(data))
    return _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.c_void_p))


def _protect(data: bytes) -> bytes:
    crypt32, kernel32 = _CRYPT
    blob_in = _blob_of(data)
    blob_out = _DATA_BLOB()
    if not crypt32.CryptProtectData(ctypes.byref(blob_in), None, None, None, None,
                                    _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out)):
        raise OSError("CryptProtectData failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def _unprotect(data: bytes) -> bytes:
    crypt32, kernel32 = _CRYPT
    blob_in = _blob_of(data)
    blob_out = _DATA_BLOB()
    descr = ctypes.c_void_p()
    if not crypt32.CryptUnprotectData(ctypes.byref(blob_in), ctypes.byref(descr), None, None,
                                      None, 0, ctypes.byref(blob_out)):
        raise OSError("CryptUnprotectData failed")
    try:
        out = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)
        if descr:
            kernel32.LocalFree(descr)
    return out


# ---------------- 对外 API ----------------

def encrypt_secret(plain: str) -> str:
    """明文 -> "dpapi:" + base64(密文)；非 Windows / DPAPI 失败降级 "plain:" + 原文。
    传入已带本模块前缀的值时原样返回（防二次包裹）。全程不抛异常。"""
    text = "" if plain is None else str(plain)
    if text.startswith(_PREFIX_ENC) or text.startswith(_PREFIX_PLAIN):
        return text
    try:
        if _CRYPT is None:
            raise OSError("DPAPI 仅 Windows 可用")
        cipher = _protect(text.encode("utf-8"))
        return _PREFIX_ENC + base64.b64encode(cipher).decode("ascii")
    except Exception:
        return _PREFIX_PLAIN + text


def decrypt_secret(stored: str) -> str:
    """还原 encrypt_secret 的存储值："dpapi:" 解密（失败返回 ""，避免脏数据外发）；
    "plain:" 去前缀；其余（旧明文数据）原样返回。全程不抛异常。"""
    text = "" if stored is None else str(stored)
    if not text.startswith(_PREFIX_ENC):
        if text.startswith(_PREFIX_PLAIN):
            return text[len(_PREFIX_PLAIN):]
        return text
    try:
        if _CRYPT is None:
            raise OSError("DPAPI 仅 Windows 可用")
        cipher = base64.b64decode(text[len(_PREFIX_ENC):])
        return _unprotect(cipher).decode("utf-8")
    except Exception:
        return ""


def is_encrypted(stored: str) -> bool:
    """值是否为本模块产出的存储格式（dpapi: 密文 / plain: 降级标记前缀）。"""
    s = "" if stored is None else str(stored)
    return s.startswith(_PREFIX_ENC) or s.startswith(_PREFIX_PLAIN)
