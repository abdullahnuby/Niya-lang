"""المُحقّق: بيشغّل example وبيكسّر ensure/require بحالات حدود وعشوائية. من غير AI، ونفس seed بيدّي نفس النتيجة."""
import hashlib
import io
import itertools
import random
import re
from dataclasses import dataclass, field

from . import nodes as nd
from .errors import ContractError, FuelError, NiyaError
from .interp import Interp
from .values import Closure, Unc, fmt_num, is_num, show_repr, values_equal

TOP_FUEL, EXAMPLE_FUEL, CASE_FUEL = 5_000_000, 500_000, 200_000
DEFAULT_RANGE = {"number": (0, 1000), "int": (0, 100)}


@dataclass
class FnReport:
    name: str
    line: int
    status: str = "verified"          # verified | failed | hole
    lines: list = field(default_factory=list)
    seal: str = None
    cases: int = 0
    rejected: int = 0


def _match(got, expected):
    if isinstance(expected, Unc):
        if isinstance(got, Unc):
            return expected.lo <= got.lo and got.hi <= expected.hi
        return is_num(got) and expected.lo <= got <= expected.hi
    if isinstance(got, Unc):
        return False
    return values_equal(got, expected)


def _axes(p, notes):
    """(قيم الحدود, دالة توليد عشوائي) للمدخل، أو None لو مش قابل للتوليد."""
    t = p.type
    if t is None:
        return None
    if t.base == "enum":
        return list(t.choices), lambda r: r.choice(t.choices)
    if t.base == "bool":
        return [True, False], lambda r: r.random() < 0.5
    if t.base in ("number", "int"):
        lo, hi = (t.lo, t.hi) if t.lo is not None else DEFAULT_RANGE[t.base]
        if t.lo is None:
            notes.append(f"المدخل {p.name} من غير مدى: افترضت {lo}..{hi} (اكتب in lo..hi لو عايز غير كده)")
        if t.base == "int":
            lo, hi = int(-(-lo // 1)), int(hi // 1)
            return sorted({lo, (lo + hi) // 2, hi}), lambda r: r.randint(lo, hi)

        def rnd(r):
            if r.random() < 0.5 and int(-(-lo // 1)) <= int(hi // 1):
                return r.randint(int(-(-lo // 1)), int(hi // 1))
            return round(r.uniform(lo, hi), 2)
        return sorted({lo, (lo + hi) / 2, hi}), rnd
    return None


def _trunc(s, n=120):
    return s if len(s) <= n else s[:n] + " …"


def _case_str(case):
    return ", ".join(f"{k}={show_repr(v)}" for k, v in case.items())


def verify_closure(it, c, seed=0, fuzz=300):
    fd = c.fn
    rep = FnReport(fd.name, fd.line)
    L = rep.lines
    if fd.hole:
        rep.status = "hole"
        L.append(("⚠", "جسمها { ? } لسه فاضي. مفيش مولّد في الإصدار ده: اكتب الجسم عشان يتحقق."))
        L.append(("·", f"الصياغة سليمة: {sum(isinstance(x, nd.Ensure) for x in fd.clauses)} ensure، "
                       f"{sum(isinstance(x, nd.Example) for x in fd.clauses)} example، لسه ما اتشغّلوش."))
        return rep

    # 1) الأمثلة
    n_ex = 0
    for cl in fd.clauses:
        if not isinstance(cl, nd.Example):
            continue
        n_ex += 1
        it.fuel = EXAMPLE_FUEL
        try:
            args = [it.ev(a, c.env) for a in cl.args]
            kwargs = {k: it.ev(v, c.env) for k, v in cl.kwargs}
            expected = it.ev(cl.expected, c.env)
            got = it.call_value(c, args, kwargs, cl.line)
        except NiyaError as e:
            L.append(("✗", f"مثال {cl.src} (سطر {cl.line}): {e.msg}"))
            rep.status = "failed"
            continue
        if _match(got, expected):
            L.append(("✓", f"مثال {cl.src}  ←  {_trunc(show_repr(got))}"))
        else:
            L.append(("✗", f"مثال {cl.src} (سطر {cl.line}): المتوقع {_trunc(show_repr(expected))} وطلع {_trunc(show_repr(got))}"))
            rep.status = "failed"

    # 2) حالات حدود + عشوائي
    notes, gens, names = [], [], []
    fuzzable = True
    for p in fd.params:
        g = _axes(p, notes)
        if g is None:
            if p.default is None:
                fuzzable = False
                L.append(("·", f"الفحص العشوائي اتخطّى: المدخل {p.name} مش نوع قابل للتوليد "
                               "(استخدم number/int in lo..hi أو bool أو قائمة قيم)"))
                break
            continue
        names.append(p.name)
        gens.append(g)
    if fuzzable:
        for nt in notes:
            L.append(("⚠", nt))
        rng = random.Random(f"{seed}:{fd.name}")
        combos = list(itertools.product(*[g[0] for g in gens])) if gens else [()]
        if len(combos) > 128:
            rng.shuffle(combos)
            combos = combos[:128]
        cases = [dict(zip(names, cmb)) for cmb in combos]
        cases += [{n: g[1](rng) for n, g in zip(names, gens)} for _ in range(fuzz)]
        fails = {}
        for case in cases:
            it.fuel = CASE_FUEL
            try:
                it.call_value(c, [], case, fd.line)
                rep.cases += 1
            except ContractError as e:
                if e.kind == "require" and e.fn is c:
                    rep.rejected += 1
                    continue
                rep.cases += 1
                who = "" if e.fn in (None, c) else f"(جوه {e.fn.name}) "
                key = (e.kind, e.line, re.sub(r"\d+(\.\d+)?", "#", e.msg))
                rec = fails.setdefault(key, [0, f"{who}{e.msg}", case])
                rec[0] += 1
            except NiyaError as e:
                rep.cases += 1
                tag = "الكود وقف" if not isinstance(e, FuelError) else "الكود خد وقت زيادة"
                key = ("err", e.line, re.sub(r"\d+(\.\d+)?", "#", e.msg))
                rec = fails.setdefault(key, [0, f"{tag}: {e.msg}" + (f" (سطر {e.line})" if e.line else ""), case])
                rec[0] += 1
        total = rep.cases
        for cnt, msg, case in fails.values():
            L.append(("✗", f"اتكسر في {cnt} من {total} حالة: {msg}"))
            L.append(("↳", f"مثال معاكس: {_case_str(case)}"))
            rep.status = "failed"
        if not fails:
            if total == 0:
                L.append(("⚠", "كل الحالات اترفضت بالشرط المسبق (require): مفيش حاجة اتفحصت"))
            else:
                extra = f"، و{rep.rejected} اترفضت بـ require" if rep.rejected else ""
                L.append(("✓", f"{total} حالة (حدود + عشوائي) من غير أي كسر في العقد{extra}"))
        if not any(isinstance(x, (nd.Ensure, nd.Example)) for x in fd.clauses) and not fd.ret:
            L.append(("·", "مفيش ensure ولا example ولا نوع ناتج: الفحص بيثبت عدم الانهيار بس"))
    if rep.status == "verified":
        rep.seal = "@blake2b:" + hashlib.blake2b(fd.text.encode(), digest_size=8).hexdigest()
    return rep


def check_file(path, seed=0, fuzz=300):
    import os
    path = os.path.abspath(path)
    out = io.StringIO()
    it = Interp(fuel=TOP_FUEL, out=out)
    menv = it.run_file(path)
    reports = []
    for cell in list(menv.vars.values()):
        c = cell[0]
        if isinstance(c, Closure) and c.file == path:
            fd = c.fn
            if fd.clauses or fd.hole or any(p.type for p in fd.params):
                reports.append(verify_closure(it, c, seed, fuzz))
    return reports, out.getvalue()
