"""Ed25519 (RFC 8032) بايثون خالص، من غير مكتبات. سقالة للتجربة: مش constant-time ومش متراجَع أمنياً.
قبل أي استخدام حقيقي بدّله بـ libsodium أو مكتبة cryptography (نفس الصيغة، فالتوقيعات متوافقة)."""
import hashlib
import os

P = 2 ** 255 - 19
Q = 2 ** 252 + 27742317777372353535851937790883648493


def _inv(x):
    return pow(x, P - 2, P)


D = -121665 * _inv(121666) % P
_SQRT_M1 = pow(2, (P - 1) // 4, P)


def _sha512_modq(s):
    return int.from_bytes(hashlib.sha512(s).digest(), "little") % Q


def _add(A, B):
    a, b = (A[1] - A[0]) * (B[1] - B[0]) % P, (A[1] + A[0]) * (B[1] + B[0]) % P
    c, d = 2 * A[3] * B[3] * D % P, 2 * A[2] * B[2] % P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % P, g * h % P, f * g % P, e * h % P)


def _mul(s, A):
    R = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            R = _add(R, A)
        A = _add(A, A)
        s >>= 1
    return R


def _equal(A, B):
    return (A[0] * B[2] - B[0] * A[2]) % P == 0 and (A[1] * B[2] - B[1] * A[2]) % P == 0


def _recover_x(y, sign):
    if y >= P:
        return None
    x2 = (y * y - 1) * _inv(D * y * y + 1) % P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (P + 3) // 8, P)
    if (x * x - x2) % P != 0:
        x = x * _SQRT_M1 % P
    if (x * x - x2) % P != 0:
        return None
    if (x & 1) != sign:
        x = P - x
    return x


_GY = 4 * _inv(5) % P
_GX = _recover_x(_GY, 0)
G = (_GX, _GY, 1, _GX * _GY % P)


def _compress(A):
    zi = _inv(A[2])
    x, y = A[0] * zi % P, A[1] * zi % P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _decompress(s):
    if len(s) != 32:
        return None
    y = int.from_bytes(s, "little")
    sign, y = y >> 255, y & ((1 << 255) - 1)
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % P)


def _expand(secret):
    h = hashlib.sha512(secret).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def new_secret():
    return os.urandom(32)


def public_key(secret):
    if len(secret) != 32:
        raise ValueError("المفتاح السري لازم 32 بايت")
    return _compress(_mul(_expand(secret)[0], G))


def sign(secret, msg):
    a, prefix = _expand(secret)
    A = _compress(_mul(a, G))
    r = _sha512_modq(prefix + msg)
    Rs = _compress(_mul(r, G))
    h = _sha512_modq(Rs + A + msg)
    return Rs + int.to_bytes((r + h * a) % Q, 32, "little")


def verify(public, msg, sig):
    """بترجّع True/False ومبترفعش استثناء على مدخلات مش سليمة."""
    try:
        if len(public) != 32 or len(sig) != 64:
            return False
        A, R = _decompress(public), _decompress(sig[:32])
        if A is None or R is None:
            return False
        s = int.from_bytes(sig[32:], "little")
        if s >= Q:
            return False
        h = _sha512_modq(sig[:32] + public + msg)
        return _equal(_mul(s, G), _add(R, _mul(h, A)))
    except Exception:
        return False
