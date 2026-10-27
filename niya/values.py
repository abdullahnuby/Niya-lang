"""القيم: أرقام (ومنها غير اليقينية Unc)، نصوص، صح/غلط، nil، قوائم، سجلات، دوال."""
import json
import math

from .errors import NiyaError


class Unc:
    """رقم غير يقيني: مدى [lo, hi] وقيمة متوقعة nom. العمليات عليه بتنقل المدى (interval arithmetic)."""
    __slots__ = ("lo", "nom", "hi")

    def __init__(self, lo, nom, hi):
        self.lo, self.nom, self.hi = lo, nom, hi


def mk_unc(lo, nom, hi):
    if lo > hi:
        lo, hi = hi, lo
    return nom if lo == hi else Unc(lo, nom, hi)


class Closure:
    __slots__ = ("fn", "env", "file")

    def __init__(self, fn, env, file):
        self.fn, self.env, self.file = fn, env, file

    @property
    def name(self):
        return self.fn.name


class Builtin:
    __slots__ = ("name", "fn")

    def __init__(self, name, fn):
        self.name, self.fn = name, fn


def is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def is_numlike(v):
    return is_num(v) or isinstance(v, Unc)


def type_of(v):
    if v is None:
        return "nil"
    if isinstance(v, bool):
        return "bool"
    if is_num(v):
        return "number"
    if isinstance(v, Unc):
        return "uncertain"
    if isinstance(v, str):
        return "text"
    if isinstance(v, list):
        return "list"
    if isinstance(v, dict):
        return "record"
    return "fn"


# ------------------------------------------------------------------ عرض
def fmt_num(x):
    if isinstance(x, float):
        if not math.isfinite(x):
            return str(x)
        if x.is_integer() and abs(x) < 1e15:
            return str(int(x))
        return format(x, ".10g")
    return str(x)


def show_repr(v):
    if v is None:
        return "nil"
    if isinstance(v, bool):
        return "true" if v else "false"
    if is_num(v):
        return fmt_num(v)
    if isinstance(v, Unc):
        return f"{fmt_num(v.nom)} [{fmt_num(v.lo)} .. {fmt_num(v.hi)}]"
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, list):
        return "[" + ", ".join(show_repr(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{" + ", ".join(f"{k}: {show_repr(x)}" for k, x in v.items()) + "}"
    if isinstance(v, Closure):
        return f"<دالة {v.name}>"
    return f"<دالة مدمجة {v.name}>"


def show(v):
    return v if isinstance(v, str) else show_repr(v)


def canon(v):
    """تمثيل ثابت للقيمة (للبصمة): نفس القيمة دايماً نفس النص."""
    if isinstance(v, dict):
        return "{" + ",".join(f"{k}:{canon(v[k])}" for k in sorted(v)) + "}"
    if isinstance(v, list):
        return "[" + ",".join(canon(x) for x in v) + "]"
    if isinstance(v, Unc):
        return f"~({fmt_num(v.lo)},{fmt_num(v.nom)},{fmt_num(v.hi)})"
    return show_repr(v)


# ------------------------------------------------------------------ مساواة ومقارنة
def values_equal(a, b):
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, Unc) or isinstance(b, Unc):
        raise NiyaError("المساواة على أرقام غير يقينية مش مسموحة: استخدم .low أو .high أو .mid")
    if is_num(a) and is_num(b):
        if isinstance(a, int) and isinstance(b, int):
            return a == b
        return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(values_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(values_equal(a[k], b[k]) for k in a)
    if type(a) is not type(b):
        return False
    if isinstance(a, (str, type(None))):
        return a == b
    return a is b


def compare(op, a, b):
    if op == "==":
        return values_equal(a, b)
    if op == "!=":
        return not values_equal(a, b)
    if isinstance(a, Unc) or isinstance(b, Unc):
        if not (is_numlike(a) and is_numlike(b)):
            raise NiyaError(f"مقارنة {op} مش ممكنة بين {type_of(a)} و{type_of(b)}")
        ua, ub = _u(a), _u(b)
        yes, no = {
            "<": (ua.hi < ub.lo, ua.lo >= ub.hi), "<=": (ua.hi <= ub.lo, ua.lo > ub.hi),
            ">": (ua.lo > ub.hi, ua.hi <= ub.lo), ">=": (ua.lo >= ub.hi, ua.hi < ub.lo),
        }[op]
        if yes:
            return True
        if no:
            return False
        raise NiyaError(f"المقارنة مش حاسمة: {show_repr(a)} و{show_repr(b)} متداخلين. "
                        "اختار صراحة: .low أو .high أو .mid")
    if (is_num(a) and is_num(b)) or (isinstance(a, str) and isinstance(b, str)):
        return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]
    raise NiyaError(f"مقارنة {op} مش ممكنة بين {type_of(a)} و{type_of(b)}")


# ------------------------------------------------------------------ حساب
def _u(v):
    return v if isinstance(v, Unc) else Unc(v, v, v)


def _unc_arith(op, a, b):
    a, b = _u(a), _u(b)
    if op == "+":
        return mk_unc(a.lo + b.lo, a.nom + b.nom, a.hi + b.hi)
    if op == "-":
        return mk_unc(a.lo - b.hi, a.nom - b.nom, a.hi - b.lo)
    if op == "*":
        c = [x * y for x in (a.lo, a.hi) for y in (b.lo, b.hi)]
        return mk_unc(min(c), a.nom * b.nom, max(c))
    if op == "/":
        if b.lo <= 0 <= b.hi:
            raise NiyaError("القسمة على قيمة غير يقينية مداها بيشمل الصفر")
        c = [x / y for x in (a.lo, a.hi) for y in (b.lo, b.hi)]
        return mk_unc(min(c), a.nom / b.nom, max(c))
    if op == "**":
        if b.lo == b.hi and float(b.nom).is_integer() and b.nom >= 0 and a.lo >= 0:
            e = int(b.nom)
            return mk_unc(a.lo ** e, a.nom ** e, a.hi ** e)
        raise NiyaError("** على رقم غير يقيني بتشتغل بأس صحيح موجب وأساس موجب بس")
    raise NiyaError(f"العملية {op} مش مدعومة على الأرقام غير اليقينية")


def arith(op, a, b):
    if isinstance(a, Unc) or isinstance(b, Unc):
        if not (is_numlike(a) and is_numlike(b)):
            raise NiyaError(f"العملية {op} للأرقام بس (جاني {type_of(a)} و{type_of(b)})")
        return _unc_arith(op, a, b)
    if op == "+":
        if is_num(a) and is_num(b):
            return a + b
        if isinstance(a, str) and isinstance(b, str):
            return a + b
        if isinstance(a, list) and isinstance(b, list):
            return a + b
        raise NiyaError(f"مش ممكن تجمع {type_of(a)} مع {type_of(b)} (حوّل بـ str() أو استخدم {{}} جوه النص)")
    if not (is_num(a) and is_num(b)):
        raise NiyaError(f"العملية {op} للأرقام بس (جاني {type_of(a)} و{type_of(b)})")
    try:
        if op == "-":
            return a - b
        if op == "*":
            return a * b
        if op == "/":
            return a / b
        if op == "//":
            return a // b
        if op == "%":
            return a % b
        if op == "**":
            if abs(b) > 1000:
                raise NiyaError("الأس كبير زيادة (الحد 1000)")
            r = a ** b
            if isinstance(r, complex):
                raise NiyaError("جذر زوجي لرقم سالب")
            return r
    except ZeroDivisionError:
        raise NiyaError("قسمة على صفر") from None
    except OverflowError:
        raise NiyaError("الرقم كبير زيادة") from None
    raise NiyaError(f"عملية مجهولة {op}")


# ------------------------------------------------------------------ الأنواع (annotations)
_WORD = {"number": "رقم", "int": "عدد صحيح", "text": "نص", "bool": "صح/غلط", "list": "قائمة",
         "record": "سجل", "fn": "دالة"}


def check_type(spec, v):
    """(True, القيمة) لو مناسبة، أو (False, رسالة)."""
    b = spec.base
    if b == "any":
        return True, v
    if b == "enum":
        for c in spec.choices:
            if not isinstance(v, (Unc, list, dict)) and type(c) is type(v) and values_equal(c, v):
                return True, v
        return False, "لازم يكون واحد من (" + " | ".join(show_repr(c) for c in spec.choices) + f") وجه {show_repr(v)}"
    ok = {
        "number": is_numlike(v), "int": is_num(v) and float(v).is_integer(), "text": isinstance(v, str),
        "bool": isinstance(v, bool), "list": isinstance(v, list), "record": isinstance(v, dict),
        "fn": isinstance(v, (Closure, Builtin)),
    }[b]
    if not ok:
        return False, f"لازم يكون {_WORD[b]} وجه {type_of(v)} ({show_repr(v)})"
    if b == "int":
        v = int(v)
    if spec.lo is not None:
        lo, hi = (v.lo, v.hi) if isinstance(v, Unc) else (v, v)
        if lo < spec.lo or hi > spec.hi:
            return False, f"برّه المدى {fmt_num(spec.lo)}..{fmt_num(spec.hi)} (وجه {show_repr(v)})"
    return True, v
