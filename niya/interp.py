"""المفسّر (tree-walking). القيم ثابتة: القوائم والسجلات ما بتتعدّلش من جوه، وlet مينفعش يتغير."""
import os
import sys

from . import nodes as nd
from .errors import ContractError, FuelError, HoleError, NiyaError, ParseError
from .parser import parse
from .values import (Closure, Builtin, Unc, arith, check_type, compare, is_num, mk_unc,
                     show, show_repr, type_of)

MAX_DEPTH = 400
UNC_FIELDS = {"low": "lo", "min": "lo", "ادنى": "lo", "high": "hi", "max": "hi", "اعلى": "hi",
              "mid": "nom", "expected": "nom", "متوقع": "nom"}


class ReturnEx(Exception):
    def __init__(self, value):
        self.value = value


class BreakEx(Exception):
    pass


class ContinueEx(Exception):
    pass


class Env:
    __slots__ = ("vars", "parent")

    def __init__(self, parent=None):
        self.vars, self.parent = {}, parent

    def lookup(self, name):
        e = self
        while e is not None:
            c = e.vars.get(name)
            if c is not None:
                return c
            e = e.parent
        return None

    def define(self, name, value, mutable=False):
        self.vars[name] = [value, mutable]


class Interp:
    def __init__(self, fuel=5_000_000, out=None):
        self.out = out or sys.stdout
        self.fuel = fuel
        self.depth = 0
        self.repl = False
        self.dirs = [os.getcwd()]
        self.files = ["<main>"]
        self.loading, self.modules = [], {}
        self.root = Env()
        from .builtins import install
        install(self)
        self._d = {cls: getattr(self, "ev_" + cls.__name__) for cls in (
            nd.Num, nd.Str, nd.Const, nd.Name, nd.ListLit, nd.RecLit, nd.Unary, nd.Bin, nd.Logic, nd.Not,
            nd.Cmp, nd.PM, nd.Between, nd.Call, nd.Index, nd.Field, nd.If, nd.FnExpr, nd.Block, nd.Let,
            nd.Assign, nd.Return, nd.Break, nd.Continue, nd.While, nd.For, nd.Use, nd.ExprStmt, nd.DefFn)}

    # ------------------------------------------------------------ أدوات
    def new_module_env(self):
        return Env(self.root)

    def tick(self, line=None):
        self.fuel -= 1
        if self.fuel < 0:
            raise FuelError("الكود خد خطوات أكتر من الحد (غالباً حلقة لا نهائية)", line)

    @staticmethod
    def _at(e, line):
        if e.line is None:
            e.line = line
        return e

    def need_bool(self, v, line, what="الشرط"):
        if not isinstance(v, bool):
            raise NiyaError(f"{what} لازم يطلع صح/غلط، وجه {type_of(v)} ({show_repr(v)})", line)
        return v

    def ev(self, n, env):
        return self._d[type(n)](n, env)

    def run_source(self, src, env, file="<repl>"):
        stmts = parse(src)
        self.files.append(file)
        try:
            res = None
            for s in stmts:
                res = self.ev(s, env)
            return res
        except ParseError as e:
            e.file = e.file or file
            raise
        finally:
            self.files.pop()

    def run_file(self, path, env=None):
        path = os.path.abspath(path)
        with open(path, encoding="utf-8-sig") as f:
            src = f.read()
        env = env or self.new_module_env()
        self.dirs.append(os.path.dirname(path))
        try:
            self.run_source(src, env, path)
        finally:
            self.dirs.pop()
        return env

    # ------------------------------------------------------------ تعبيرات
    def ev_Num(self, n, env):
        return n.value

    def ev_Const(self, n, env):
        return n.value

    def ev_Str(self, n, env):
        return "".join(p if isinstance(p, str) else show(self.ev(p, env)) for p in n.parts)

    def ev_Name(self, n, env):
        c = env.lookup(n.name)
        if c is None:
            raise NiyaError(f"اسم مش معروف: {n.name}", n.line)
        return c[0]

    def ev_ListLit(self, n, env):
        return [self.ev(x, env) for x in n.items]

    def ev_RecLit(self, n, env):
        return {k: self.ev(v, env) for k, v in n.pairs}

    def ev_Unary(self, n, env):
        v = self.ev(n.operand, env)
        if is_num(v):
            return -v if n.op == "-" else v
        if isinstance(v, Unc):
            return mk_unc(-v.hi, -v.nom, -v.lo) if n.op == "-" else v
        raise NiyaError(f"العلامة {n.op} للأرقام بس، وجه {type_of(v)}", n.line)

    def ev_Bin(self, n, env):
        l, r = self.ev(n.left, env), self.ev(n.right, env)
        try:
            return arith(n.op, l, r)
        except NiyaError as e:
            raise self._at(e, n.line)

    def ev_Logic(self, n, env):
        l = self.need_bool(self.ev(n.left, env), n.line, f"طرف {n.op}")
        if (n.op == "and") != l:
            return l
        return self.need_bool(self.ev(n.right, env), n.line, f"طرف {n.op}")

    def ev_Not(self, n, env):
        return not self.need_bool(self.ev(n.operand, env), n.line, "اللي بعد not")

    def ev_Cmp(self, n, env):
        left = self.ev(n.first, env)
        for op, node in n.rest:
            right = self.ev(node, env)
            try:
                ok = compare(op, left, right)
            except NiyaError as e:
                raise self._at(e, n.line)
            if not ok:
                return False
            left = right
        return True

    def ev_PM(self, n, env):
        base, amt = self.ev(n.base, env), self.ev(n.amount, env)
        if not is_num(base):
            raise NiyaError("اللي قبل ± لازم يكون رقم عادي", n.line)
        if not is_num(amt) or amt < 0:
            raise NiyaError("اللي بعد ± لازم يكون رقم موجب", n.line)
        d = abs(base) * amt / 100 if n.percent else amt
        return mk_unc(base - d, base, base + d)

    def ev_Between(self, n, env):
        lo, hi = self.ev(n.lo, env), self.ev(n.hi, env)
        if not (is_num(lo) and is_num(hi)) or lo > hi:
            raise NiyaError("lo .. hi لازم يكونوا رقمين والأول أصغر", n.line)
        return mk_unc(lo, (lo + hi) / 2, hi)

    def ev_Index(self, n, env):
        o, i = self.ev(n.obj, env), self.ev(n.idx, env)
        if isinstance(o, dict):
            if not isinstance(i, str):
                raise NiyaError("مفاتيح السجل نصوص", n.line)
            if i not in o:
                raise NiyaError(f"مفيش مفتاح {show_repr(i)} في السجل (الموجود: {', '.join(o) or 'فاضي'})", n.line)
            return o[i]
        if isinstance(o, (list, str)):
            if not is_num(i) or not float(i).is_integer():
                raise NiyaError("الفهرس لازم يكون عدد صحيح", n.line)
            i = int(i)
            if not -len(o) <= i < len(o):
                raise NiyaError(f"الفهرس {i} برّه الحدود (الطول {len(o)})", n.line)
            return o[i]
        raise NiyaError(f"مش ممكن [] على {type_of(o)}", n.line)

    def ev_Field(self, n, env):
        o = self.ev(n.obj, env)
        if isinstance(o, dict):
            if n.name not in o:
                raise NiyaError(f"السجل مفيهوش الحقل {n.name} (الموجود: {', '.join(o) or 'فاضي'})", n.line)
            return o[n.name]
        if isinstance(o, Unc) and n.name in UNC_FIELDS:
            return getattr(o, UNC_FIELDS[n.name])
        if is_num(o) and n.name in UNC_FIELDS:
            return o
        raise NiyaError(f"{type_of(o)} مفيهوش حقل {n.name}", n.line)

    def ev_If(self, n, env):
        if self.need_bool(self.ev(n.cond, env), n.line, "شرط if"):
            return self.ev_Block(n.then, env)
        if n.else_ is None:
            return None
        return self.ev(n.else_, env)

    def ev_FnExpr(self, n, env):
        return Closure(n.fn, env, self.files[-1])

    # ------------------------------------------------------------ الاستدعاء
    def ev_Call(self, n, env):
        f = self.ev(n.fn, env)
        args = [self.ev(a, env) for a in n.args]
        kwargs = {k: self.ev(v, env) for k, v in n.kwargs}
        return self.call_value(f, args, kwargs, n.line)

    def call_value(self, f, args, kwargs=None, line=None):
        if isinstance(f, Closure):
            return self.call_closure(f, args, kwargs or {}, line)
        if isinstance(f, Builtin):
            if kwargs:
                raise NiyaError(f"{f.name} مبتقبلش مدخلات بالاسم", line)
            try:
                return f.fn(*args)
            except NiyaError as e:
                raise self._at(e, line)
            except TypeError as e:
                raise NiyaError(f"استخدام غلط لـ {f.name}: {e}", line) from None
        raise NiyaError(f"مش دالة: {show_repr(f)}", line)

    def call_closure(self, c, args, kwargs, line):
        fd = c.fn
        if fd.hole:
            raise HoleError(f"{fd.name}: جسمها {{ ? }} لسه فاضي (ثغرة)", line)
        self.tick(line)
        if self.depth >= MAX_DEPTH:
            raise NiyaError("استدعاءات متداخلة كتير (recursion عميق زيادة)", line)
        names = [p.name for p in fd.params]
        if len(args) > len(names):
            raise NiyaError(f"{fd.name} بتاخد {len(names)} مدخل بس وانت بعت {len(args)}", line)
        bound = dict(zip(names, args))
        for k, v in kwargs.items():
            if k not in names:
                raise NiyaError(f"{fd.name} مفيهاش مدخل اسمه {k} (المدخلات: {', '.join(names) or 'مفيش'})", line)
            if k in bound:
                raise NiyaError(f"المدخل {k} اتبعت مرتين", line)
            bound[k] = v
        env = Env(c.env)
        for p in fd.params:
            if p.name in bound:
                v = bound[p.name]
            elif p.default is not None:
                v = self.ev(p.default, env)
            else:
                raise NiyaError(f"{fd.name}: ناقص المدخل {p.name}", line)
            if p.type:
                ok, v = check_type(p.type, v)
                if not ok:
                    raise ContractError(f"{fd.name}: المدخل {p.name} {v}", line, "input", c)
            env.define(p.name, v)
        for cl in fd.clauses:
            if isinstance(cl, nd.Require):
                if not self.need_bool(self.ev(cl.expr, env), cl.line, "الشرط المسبق"):
                    raise ContractError(f"{fd.name}: شرط مسبق اتكسر: {cl.src}", cl.line, "require", c)
        self.depth += 1
        try:
            val = self.ev_Block(fd.body, env)
        except ReturnEx as r:
            val = r.value
        except (BreakEx, ContinueEx):
            raise NiyaError("break/continue برّه حلقة", fd.line) from None
        finally:
            self.depth -= 1
        if fd.ret:
            ok, val = check_type(fd.ret, val)
            if not ok:
                raise ContractError(f"{fd.name}: الناتج {val}", fd.line, "return", c)
        post = [cl for cl in fd.clauses if isinstance(cl, nd.Ensure)]
        if post:
            env2 = Env(env)
            env2.define("result", val)
            env2.define("ناتج", val)
            for cl in post:
                if not self.need_bool(self.ev(cl.expr, env2), cl.line, "الضمان"):
                    raise ContractError(f"{fd.name}: ضمان اتكسر: {cl.src} (الناتج {show_repr(val)})",
                                        cl.line, "ensure", c)
        return val

    # ------------------------------------------------------------ جمل
    def ev_Block(self, n, env):
        env, res = Env(env), None
        for s in n.stmts:
            res = self.ev(s, env)
        return res

    def ev_ExprStmt(self, n, env):
        return self.ev(n.expr, env)

    def _define(self, env, name, value, mutable, line):
        if name in env.vars and not self.repl:
            raise NiyaError(f"{name} اتعرّف قبل كده في نفس المكان (القيم ما بتتغيرش، اختار اسم تاني أو استخدم var)", line)
        env.define(name, value, mutable)

    def ev_Let(self, n, env):
        self._define(env, n.name, self.ev(n.expr, env), n.mutable, n.line)

    def ev_DefFn(self, n, env):
        self._define(env, n.fn.name, Closure(n.fn, env, self.files[-1]), False, n.line)

    def ev_Assign(self, n, env):
        cell = env.lookup(n.name)
        if cell is None:
            raise NiyaError(f"اسم مش معروف: {n.name} (اعمله var الأول)", n.line)
        if not cell[1]:
            raise NiyaError(f"{n.name} متعرّف بـ let فمينفعش يتغير. اعمله var لو عايز تغيّره", n.line)
        v = self.ev(n.expr, env)
        if n.op != "=":
            try:
                v = arith(n.op[0], cell[0], v)
            except NiyaError as e:
                raise self._at(e, n.line)
        cell[0] = v

    def ev_Return(self, n, env):
        raise ReturnEx(None if n.expr is None else self.ev(n.expr, env))

    def ev_Break(self, n, env):
        raise BreakEx()

    def ev_Continue(self, n, env):
        raise ContinueEx()

    def ev_While(self, n, env):
        while True:
            self.tick(n.line)
            if not self.need_bool(self.ev(n.cond, env), n.line, "شرط while"):
                return None
            try:
                self.ev_Block(n.body, env)
            except BreakEx:
                return None
            except ContinueEx:
                continue

    def ev_For(self, n, env):
        it = self.ev(n.iter, env)
        if isinstance(it, dict):
            it = list(it)
        elif not isinstance(it, (list, str)):
            raise NiyaError(f"for بتلف على قائمة أو نص أو سجل، وجه {type_of(it)}", n.line)
        for item in list(it):
            self.tick(n.line)
            e = Env(env)
            if len(n.names) == 1:
                e.define(n.names[0], item)
            else:
                if not isinstance(item, list) or len(item) != len(n.names):
                    raise NiyaError(f"العنصر {show_repr(item)} مش قائمة من {len(n.names)} عناصر", n.line)
                for nm, v in zip(n.names, item):
                    e.define(nm, v)
            try:
                self.ev_Block(n.body, e)
            except BreakEx:
                break
            except ContinueEx:
                continue
        return None

    def ev_Use(self, n, env):
        path = os.path.join(self.dirs[-1], n.path)
        if not os.path.exists(path) and os.path.exists(path + ".nia"):
            path += ".nia"
        path = os.path.abspath(path)
        if path in self.loading:
            raise NiyaError(f"use دايرية: {n.path} بيستدعي نفسه", n.line)
        if path not in self.modules:
            self.loading.append(path)
            try:
                menv = self.run_file(path)
            except OSError as e:
                raise NiyaError(f"مقدرتش أفتح {n.path}: {e.strerror}", n.line) from None
            except NiyaError as e:
                e.file = e.file or path
                raise
            finally:
                self.loading.pop()
            self.modules[path] = {k: c[0] for k, c in menv.vars.items() if not k.startswith("_")}
        for k, v in self.modules[path].items():
            self._define(env, k, v, False, n.line)
