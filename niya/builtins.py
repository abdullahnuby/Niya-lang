"""المكتبة الأساسية: دوال مدمجة (معظمها له اسم عربي). كلها بترجّع قيم جديدة ومبتعدّلش اللي جاها."""
import hashlib
import math

from .errors import NiyaError
from .values import (Builtin, Unc, arith, canon, compare, is_num, is_numlike, mk_unc, show,
                     show_repr, type_of, values_equal)


def _need(v, kind, fname):
    ok = {"list": isinstance(v, list), "text": isinstance(v, str), "record": isinstance(v, dict),
          "num": is_num(v), "fn": callable(getattr(v, "fn", None)) or hasattr(v, "env")}[kind]
    if not ok:
        raise NiyaError(f"{fname}: المدخل لازم يكون {kind} وجه {type_of(v)}")
    return v


def _mono(f, v, fname):
    """دالة متزايدة (round/floor/ceil/sqrt) على رقم أو رقم غير يقيني."""
    if isinstance(v, Unc):
        return mk_unc(f(v.lo), f(v.nom), f(v.hi))
    if is_num(v):
        return f(v)
    raise NiyaError(f"{fname}: لازم رقم وجه {type_of(v)}")


def install(it):
    B = {}

    def reg(name, fn, *aliases):
        b = Builtin(name, fn)
        for nm in (name,) + aliases:
            B[nm] = b

    call = it.call_value

    def _print(*a):
        it.out.write(" ".join(show(x) for x in a) + "\n")

    def _len(x):
        if not isinstance(x, (list, str, dict)):
            raise NiyaError(f"len: مش بتشتغل على {type_of(x)}")
        return len(x)

    def _range(a, b=None, step=1):
        if b is None:
            a, b = 0, a
        if not all(is_num(x) and float(x).is_integer() for x in (a, b, step)) or step == 0:
            raise NiyaError("range: لازم أعداد صحيحة والخطوة مش صفر")
        r = range(int(a), int(b), int(step))
        if len(r) > 2_000_000:
            raise NiyaError("range: أكبر من 2,000,000 عنصر")
        it.fuel -= len(r)
        return list(r)

    def _sum(xs, start=0):
        t = start
        for x in _need(xs, "list", "sum"):
            t = arith("+", t, x)
        return t

    def _map(xs, f):
        return [call(f, [x]) for x in _need(xs, "list", "map")]

    def _filter(xs, f):
        out = []
        for x in _need(xs, "list", "filter"):
            r = call(f, [x])
            if not isinstance(r, bool):
                raise NiyaError(f"filter: الدالة لازم ترجّع صح/غلط وجه {type_of(r)}")
            if r:
                out.append(x)
        return out

    def _reduce(xs, f, *init):
        _need(xs, "list", "reduce")
        if init:
            acc, rest = init[0], xs
        elif xs:
            acc, rest = xs[0], xs[1:]
        else:
            raise NiyaError("reduce: قائمة فاضية من غير قيمة ابتدائية")
        for x in rest:
            acc = call(f, [acc, x])
        return acc

    def _pick(name, op):
        def f(*a):
            xs = a[0] if len(a) == 1 and isinstance(a[0], list) else list(a)
            if not xs:
                raise NiyaError(f"{name}: مفيش قيم")
            if not all(is_numlike(x) for x in xs):
                raise NiyaError(f"{name}: أرقام بس")
            pick = min if name == "min" else max
            if any(isinstance(x, Unc) for x in xs):
                us = [x if isinstance(x, Unc) else Unc(x, x, x) for x in xs]
                return mk_unc(pick(u.lo for u in us), pick(u.nom for u in us), pick(u.hi for u in us))
            return pick(xs)
        return f

    def _abs(x):
        if isinstance(x, Unc):
            if x.lo >= 0:
                return x
            if x.hi <= 0:
                return mk_unc(-x.hi, abs(x.nom), -x.lo)
            return mk_unc(0, abs(x.nom), max(-x.lo, x.hi))
        return abs(_mono(lambda v: v, x, "abs"))

    def _round(x, nd=0):
        k = 10 ** int(nd)

        def r(v):
            q = math.floor(abs(v) * k + 0.5) / k
            q = q if v >= 0 else -q
            return int(q) if nd == 0 else q
        return _mono(r, x, "round")

    def _sqrt(x):
        if (x.lo if isinstance(x, Unc) else x) < 0:
            raise NiyaError("sqrt: رقم سالب")
        return _mono(math.sqrt, x, "sqrt")

    def _num(x):
        if is_numlike(x):
            return x
        try:
            return float(x) if "." in str(x) else int(x)
        except (TypeError, ValueError):
            raise NiyaError(f"num: مش قادر أحوّل {show_repr(x)} لرقم") from None

    def _int(x):
        v = _num(x)
        if isinstance(v, Unc):
            raise NiyaError("int: على رقم غير يقيني استخدم round/floor/ceil")
        return int(v)

    def _keys(r):
        return list(_need(r, "record", "keys"))

    def _has(c, k):
        if isinstance(c, dict):
            return k in c
        if isinstance(c, str):
            return isinstance(k, str) and k in c
        if isinstance(c, list):
            return any(not isinstance(x, Unc) and type(x) is type(k) and values_equal(x, k) for x in c)
        raise NiyaError(f"has: مش بتشتغل على {type_of(c)}")

    def _get(r, k, default=None):
        return _need(r, "record", "get").get(k, default)

    def _set(r, k, v):
        if not isinstance(k, str):
            raise NiyaError("set: المفتاح لازم نص")
        d = dict(_need(r, "record", "set"))
        d[k] = v
        return d

    def _push(xs, v):
        return list(_need(xs, "list", "push")) + [v]

    def _slice(xs, a=0, b=None):
        if not isinstance(xs, (list, str)):
            raise NiyaError("slice: قائمة أو نص")
        return xs[int(a):None if b is None else int(b)]

    def _sorted(xs, desc=False):
        _need(xs, "list", "sort")
        try:
            return sorted(xs, reverse=bool(desc))
        except TypeError:
            raise NiyaError("sort: العناصر لازم تكون من نوع واحد (أرقام أو نصوص)") from None

    def _sort_by(xs, f):
        keyed = [(call(f, [x]), x) for x in _need(xs, "list", "sort_by")]
        try:
            keyed.sort(key=lambda p: p[0])
        except TypeError:
            raise NiyaError("sort_by: المفاتيح لازم تكون من نوع واحد") from None
        return [x for _, x in keyed]

    def _join(xs, sep=""):
        return sep.join(show(x) for x in _need(xs, "list", "join"))

    def _split(s, sep=" "):
        return _need(s, "text", "split").split(sep)

    def _assert(c, msg="assert اتكسر"):
        if not isinstance(c, bool):
            raise NiyaError("assert: الشرط لازم صح/غلط")
        if not c:
            raise NiyaError(str(msg))

    def _error(msg="خطأ"):
        raise NiyaError(show(msg))

    def _all(xs):
        return all(x is True for x in _need(xs, "list", "all"))

    def _hash(x):
        return "@blake2b:" + hashlib.blake2b(canon(x).encode(), digest_size=8).hexdigest()

    reg("print", _print, "اطبع")
    reg("len", _len, "طول")
    reg("range", _range, "مدى")
    reg("sum", _sum, "مجموع")
    reg("map", _map, "حول")
    reg("filter", _filter, "رشح")
    reg("reduce", _reduce, "اختزل")
    reg("min", _pick("min", "<"), "اصغر")
    reg("max", _pick("max", ">"), "اكبر")
    reg("abs", _abs, "مطلق")
    reg("round", _round, "قرب")
    reg("floor", lambda x: _mono(math.floor, x, "floor"))
    reg("ceil", lambda x: _mono(math.ceil, x, "ceil"))
    reg("sqrt", _sqrt)
    reg("str", show, "نص")
    reg("num", _num)
    reg("int", _int)
    reg("type", type_of, "نوع")
    reg("keys", _keys, "مفاتيح")
    reg("values", lambda r: list(_need(r, "record", "values").values()), "قيم")
    reg("has", _has, "فيه")
    reg("get", _get, "هات")
    reg("set", _set, "ضع")
    reg("push", _push, "ضيف")
    reg("slice", _slice, "قطع")
    reg("sort", _sorted, "رتب")
    reg("sort_by", _sort_by)
    reg("reverse", lambda xs: list(reversed(_need(xs, "list", "reverse"))), "اعكس")
    reg("join", _join, "ضم")
    reg("split", _split, "قسم")
    reg("upper", lambda s: _need(s, "text", "upper").upper())
    reg("lower", lambda s: _need(s, "text", "lower").lower())
    reg("trim", lambda s: _need(s, "text", "trim").strip())
    reg("replace", lambda s, a, b: _need(s, "text", "replace").replace(a, b))
    reg("enumerate", lambda xs: [[i, x] for i, x in enumerate(_need(xs, "list", "enumerate"))])
    reg("zip", lambda a, b: [[x, y] for x, y in zip(_need(a, "list", "zip"), _need(b, "list", "zip"))])
    reg("any", lambda xs: any(x is True for x in _need(xs, "list", "any")))
    reg("all", _all)
    reg("assert", _assert, "تاكد")
    reg("error", _error, "خطأ")
    reg("hash", _hash, "بصمة")

    from .world_builtins import install_world
    install_world(it, reg)

    for name, b in B.items():
        it.root.define(name, b)
