"""الصيغة على السلك: قيم نية <-> JSON، رسائل موقّعة، بصمة المحتوى، وتأطير TCP.
الدوال مبتتبعتش أبداً (البوابة بتعلن بيانات بس)، والسر مينفعش يتبعت."""
import hashlib
import json
import math
import struct

from . import crypto
from .errors import NiyaError
from .values import Secret, Unc, is_num, mk_unc

ID_PREFIX = "bab:ed25519:"
CID_PREFIX = "bab:blake2b:"
MAX_MSG = 1_048_576
MAX_DEPTH = 40


def to_json(v, depth=0):
    if depth > MAX_DEPTH:
        raise NiyaError("القيمة متداخلة زيادة")
    if isinstance(v, Secret):
        raise NiyaError("السر (المفتاح الخاص) مينفعش يتبعت ولا يتخزن")
    if v is None or isinstance(v, (bool, str)):
        return v
    if is_num(v):
        if isinstance(v, float) and not math.isfinite(v):
            raise NiyaError("الرقم لازم يكون منتهي (مش inf ولا nan)")
        return v
    if isinstance(v, Unc):
        return {"$unc": [v.lo, v.nom, v.hi]}
    if isinstance(v, list):
        return [to_json(x, depth + 1) for x in v]
    if isinstance(v, dict):
        out = {}
        for k, x in v.items():
            if k.startswith("$"):
                raise NiyaError(f"اسم الحقل {k} محجوز (بيبدأ بـ $)")
            out[k] = to_json(x, depth + 1)
        return out
    raise NiyaError("الدوال مبتتبعتش على الشبكة (البوابة بتعلن بيانات بس)")


def from_json(x, depth=0):
    if depth > MAX_DEPTH:
        raise NiyaError("رسالة متداخلة زيادة")
    if x is None or isinstance(x, (bool, str)):
        return x
    if isinstance(x, (int, float)):
        return x
    if isinstance(x, list):
        return [from_json(y, depth + 1) for y in x]
    if isinstance(x, dict):
        if "$unc" in x:
            u = x["$unc"]
            if len(x) != 1 or not isinstance(u, list) or len(u) != 3 or not all(is_num(n) for n in u):
                raise NiyaError("رقم غير يقيني بصيغة غلط")
            return mk_unc(u[0], u[1], u[2])
        if any(k.startswith("$") for k in x):
            raise NiyaError("حقل محجوز في الرسالة")
        return {k: from_json(y, depth + 1) for k, y in x.items()}
    raise NiyaError("نوع مش مدعوم في الرسالة")


def canon_json(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def cid(value):
    return CID_PREFIX + hashlib.blake2b(canon_json(to_json(value)), digest_size=32).hexdigest()


def cid_of_json(obj):
    return CID_PREFIX + hashlib.blake2b(canon_json(obj), digest_size=32).hexdigest()


# ---------------------------------------------------------------- الهوية والتوقيع
def new_key():
    secret = crypto.new_secret()
    return {"id": ID_PREFIX + crypto.public_key(secret).hex(), "secret": Secret(secret.hex())}


def pub_from_id(ident):
    if not isinstance(ident, str) or not ident.startswith(ID_PREFIX):
        return None
    h = ident[len(ID_PREFIX):]
    if len(h) != 64:
        return None
    try:
        return bytes.fromhex(h)
    except ValueError:
        return None


def check_key(key):
    if not (isinstance(key, dict) and isinstance(key.get("id"), str) and isinstance(key.get("secret"), str)):
        raise NiyaError("المفتاح لازم يكون سجل فيه id وsecret (من key_new أو key_open)")
    try:
        sec = bytes.fromhex(key["secret"])
    except ValueError:
        raise NiyaError("المفتاح السري مش hex سليم") from None
    if len(sec) != 32 or ID_PREFIX + crypto.public_key(sec).hex() != key["id"]:
        raise NiyaError("المفتاح السري مش مطابق للـ id")
    return sec


def sign_fields(key, fields):
    """بيوقّع dict (JSON) ويضيف from وsig. التوقيع على الشكل الثابت بدون sig."""
    sec = check_key(key)
    body = dict(fields)
    body["from"] = key["id"]
    body["sig"] = crypto.sign(sec, canon_json(body)).hex()
    return body


def check_signed(obj):
    """True لو obj قاموس موقّع صح بهوية from."""
    try:
        if not isinstance(obj, dict) or not isinstance(obj.get("sig"), str):
            return False
        pub = pub_from_id(obj.get("from"))
        if pub is None:
            return False
        rest = {k: v for k, v in obj.items() if k != "sig"}
        return crypto.verify(pub, canon_json(rest), bytes.fromhex(obj["sig"]))
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------- التأطير
def send_msg(sock, obj):
    data = json.dumps(obj, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(data) > MAX_MSG:
        raise NiyaError("الرسالة أكبر من الحد (1MB)")
    sock.sendall(struct.pack(">I", len(data)) + data)


def _read(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("الاتصال اتقفل قبل ما الرسالة تكمل")
        buf += chunk
    return buf


def recv_msg(sock):
    (n,) = struct.unpack(">I", _read(sock, 4))
    if n > MAX_MSG:
        raise ValueError("رسالة أكبر من الحد")

    def bad(c):
        raise ValueError("ثابت JSON مش مسموح")
    return json.loads(_read(sock, n).decode("utf-8"), parse_constant=bad)
