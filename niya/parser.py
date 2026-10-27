"""parser للغة نية: بيطلّع شجرة من nodes.py. التعبيرات فيها أولويات، والجمل بتتفصل بسطر جديد أو ;"""
from .errors import ParseError
from .lexer import lex
from .nodes import *  # noqa: F401,F403

TYPE_WORDS = {
    "number": "number", "رقم": "number", "int": "int", "عدد": "int", "text": "text", "نص": "text",
    "bool": "bool", "منطقي": "bool", "list": "list", "قائمة": "list", "record": "record",
    "سجل": "record", "any": "any", "اي": "any",
}
CMP_OPS = ("==", "!=", "<", ">", "<=", ">=")
ASSIGN_OPS = ("=", "+=", "-=", "*=", "/=")


def _desc(t):
    if t.kind == "EOF":
        return "نهاية الملف"
    if t.kind == "NL":
        return "نهاية السطر"
    if t.kind == "STR":
        return "نص"
    return repr(t.value) if t.kind in ("ID", "NUM", "PCT") else str(t.value)


class Parser:
    def __init__(self, src, line0=1):
        self.src = src
        self.toks = lex(src, line0)
        self.i = 0
        self.last_end = 0

    # ---------------------------------------------------------- أدوات
    def peek(self, k=0):
        return self.toks[min(self.i + k, len(self.toks) - 1)]

    def next(self):
        t = self.toks[self.i]
        if t.kind != "EOF":
            self.i += 1
        self.last_end = t.end
        return t

    def is_op(self, v, k=0):
        t = self.peek(k)
        return t.kind == "OP" and t.value == v

    def is_kw(self, v, k=0):
        t = self.peek(k)
        return t.kind == "KW" and t.value == v

    def accept_op(self, v):
        if self.is_op(v):
            self.next()
            return True
        return False

    def error(self, msg, tok=None):
        tok = tok or self.peek()
        e = ParseError(msg, tok.line)
        e.eof = tok.kind == "EOF"
        return e

    def expect_op(self, v, why=""):
        if not self.is_op(v):
            raise self.error(f"المفروض {v!r}{why}، لقيت {_desc(self.peek())}")
        return self.next()

    def expect_ident(self):
        t = self.peek()
        if t.kind == "KW":
            raise self.error(f"الكلمة {t.value!r} محجوزة في اللغة، اختار اسم تاني")
        if t.kind != "ID":
            raise self.error(f"المفروض اسم، لقيت {_desc(t)}")
        return self.next().value

    def skip_nl(self):
        while self.peek().kind == "NL":
            self.next()

    def end_stmt(self):
        t = self.peek()
        if t.kind == "NL" or (t.kind == "OP" and t.value == ";"):
            self.next()
        elif t.kind == "EOF" or (t.kind == "OP" and t.value == "}"):
            return
        else:
            raise self.error(f"مش متوقع {_desc(t)} هنا، المفروض نهاية السطر")

    # ---------------------------------------------------------- جمل
    def parse_program(self):
        stmts = []
        self.skip_nl()
        while self.peek().kind != "EOF":
            stmts.append(self.parse_stmt())
            self.end_stmt()
            self.skip_nl()
        return stmts

    def parse_block(self, allow_hole=False):
        t = self.expect_op("{", " عشان يبدأ الجسم")
        self.skip_nl()
        if allow_hole and self.is_op("?"):
            self.next()
            self.skip_nl()
            self.expect_op("}")
            return "HOLE"
        stmts = []
        while not self.is_op("}"):
            if self.peek().kind == "EOF":
                raise self.error("الجسم مش مقفول: ناقص }")
            stmts.append(self.parse_stmt())
            self.end_stmt()
            self.skip_nl()
        self.next()
        return Block(stmts, t.line)

    def parse_stmt(self):
        t = self.peek()
        line = t.line
        if t.kind == "KW":
            k = t.value
            if k in ("let", "var"):
                self.next()
                name = self.expect_ident()
                self.expect_op("=", " بعد الاسم")
                return Let(name, self.parse_expr(), k == "var", line)
            if k == "fn" and self.peek(1).kind == "ID":
                return DefFn(self.parse_fn(True), line)
            if k == "return":
                self.next()
                n = self.peek()
                if n.kind in ("NL", "EOF") or (n.kind == "OP" and n.value in ("}", ";")):
                    return Return(None, line)
                return Return(self.parse_expr(), line)
            if k in ("break", "continue"):
                self.next()
                return (Break if k == "break" else Continue)(line)
            if k == "while":
                self.next()
                cond = self.parse_expr()
                return While(cond, self.parse_block(), line)
            if k == "for":
                self.next()
                names = [self.expect_ident()]
                while self.accept_op(","):
                    names.append(self.expect_ident())
                if not self.is_kw("in"):
                    raise self.error("المفروض in بعد اسم المتغير في for")
                self.next()
                it = self.parse_expr()
                return For(names, it, self.parse_block(), line)
            if k == "use":
                self.next()
                s = self.peek()
                if s.kind != "STR" or len(s.value) != 1 or not isinstance(s.value[0], str):
                    raise self.error("use بتاخد مسار ملف بين علامتين تنصيص")
                self.next()
                return Use(s.value[0], line)
            if k in ("require", "ensure", "example"):
                raise self.error(f"{k} مكانها بين رأس الدالة وجسمها، مش لوحدها")
        e = self.parse_expr()
        n = self.peek()
        if n.kind == "OP" and n.value in ASSIGN_OPS:
            if not isinstance(e, Name):
                raise self.error("القيم ما بتتعدّلش من جوه (immutable): استخدم set() أو push() وخزّن الناتج في var", n)
            self.next()
            return Assign(e.name, n.value, self.parse_expr(), line)
        return ExprStmt(e, line)

    # ---------------------------------------------------------- الدوال والعقود
    def parse_type(self):
        t = self.peek()
        if t.kind in ("STR", "NUM") or (t.kind == "KW" and t.value in ("true", "false")) or self.is_op("-"):
            choices = [self.parse_literal()]
            while self.accept_op("|"):
                choices.append(self.parse_literal())
            return TypeSpec("enum", choices=choices)
        word = "fn" if (t.kind == "KW" and t.value == "fn") else (t.value if t.kind == "ID" else None)
        base = TYPE_WORDS.get(word, word if word == "fn" else None)
        if base is None:
            raise self.error(f"نوع مش معروف: {_desc(t)} (المتاح: number int text bool list record any، أو قيم زي \"a\" | \"b\")")
        self.next()
        lo = hi = None
        if self.is_kw("in"):
            if base not in ("number", "int"):
                raise self.error("in lo..hi للأرقام بس")
            self.next()
            lo = self.parse_signed_num()
            self.expect_op("..")
            hi = self.parse_signed_num()
            if lo > hi:
                raise self.error("المدى مقلوب")
        return TypeSpec(base, lo, hi)

    def parse_signed_num(self):
        neg = self.accept_op("-")
        t = self.peek()
        if t.kind != "NUM":
            raise self.error(f"المفروض رقم، لقيت {_desc(t)}")
        self.next()
        return -t.value if neg else t.value

    def parse_literal(self):
        t = self.peek()
        if t.kind == "STR":
            if len(t.value) != 1 or not isinstance(t.value[0], str):
                raise self.error("القيمة هنا لازم تكون نص عادي من غير {}")
            self.next()
            return t.value[0]
        if t.kind == "KW" and t.value in ("true", "false"):
            self.next()
            return t.value == "true"
        return self.parse_signed_num()

    def parse_params(self):
        params = []
        self.skip_nl()
        while not self.is_op(")"):
            line = self.peek().line
            name = self.expect_ident()
            ty = default = None
            if self.accept_op(":"):
                ty = self.parse_type()
            if self.accept_op("="):
                default = self.parse_expr()
            params.append(Param(name, ty, default, line))
            if not self.accept_op(","):
                break
            self.skip_nl()
        self.expect_op(")", " بعد المدخلات")
        return params

    def parse_call_args(self):
        args, kwargs = [], []
        while not self.is_op(")"):
            if self.peek().kind == "ID" and self.is_op(":", 1):
                name = self.next().value
                self.next()
                kwargs.append((name, self.parse_expr()))
            else:
                if kwargs:
                    raise self.error("مدخل عادي مينفعش ييجي بعد مدخل بالاسم")
                args.append(self.parse_expr())
            if not self.accept_op(","):
                break
        self.expect_op(")", " في آخر المدخلات")
        return args, kwargs

    def parse_clause(self):
        t = self.next()
        p0 = self.peek().pos
        if t.value in ("require", "ensure"):
            e = self.parse_expr()
            return (Require if t.value == "require" else Ensure)(e, self.src[p0:self.last_end], t.line)
        self.expect_op("(", " بعد example")
        args, kwargs = self.parse_call_args()
        self.expect_op("->", " بعد مدخلات المثال")
        expected = self.parse_expr()
        return Example(args, kwargs, expected, self.src[p0:self.last_end], t.line)

    def parse_fn(self, named):
        t0 = self.next()
        name = self.expect_ident() if named else "<دالة>"
        self.expect_op("(", " بعد fn")
        params = self.parse_params()
        ret = self.parse_type() if self.accept_op("->") else None
        clauses = []
        while True:
            self.skip_nl()
            t = self.peek()
            if t.kind == "KW" and t.value in ("require", "ensure", "example"):
                clauses.append(self.parse_clause())
            else:
                break
        hole = False
        if self.accept_op("=>"):
            e = self.parse_expr()
            body = Block([ExprStmt(e, e_line(e, t0.line))], t0.line)
        elif self.is_op("{"):
            body = self.parse_block(True)
            if body == "HOLE":
                hole, body = True, Block([], t0.line)
        else:
            raise self.error(f"المفروض {{ أو => عشان يبدأ جسم الدالة، لقيت {_desc(self.peek())}")
        return FnDef(name, params, clauses, body, ret, self.src[t0.pos:self.last_end], t0.line, hole)

    # ---------------------------------------------------------- تعبيرات
    def parse_expr(self):
        return self.parse_or()

    def parse_or(self):
        e = self.parse_and()
        while self.is_kw("or"):
            t = self.next()
            e = Logic("or", e, self.parse_and(), t.line)
        return e

    def parse_and(self):
        e = self.parse_not()
        while self.is_kw("and"):
            t = self.next()
            e = Logic("and", e, self.parse_not(), t.line)
        return e

    def parse_not(self):
        if self.is_kw("not"):
            t = self.next()
            return Not(self.parse_not(), t.line)
        return self.parse_cmp()

    def parse_cmp(self):
        first = self.parse_pm()
        rest = []
        while self.peek().kind == "OP" and self.peek().value in CMP_OPS:
            op = self.next()
            rest.append((op.value, self.parse_pm()))
        return Cmp(first, rest, self.toks[self.i - 1].line) if rest else first

    def parse_pm(self):
        e = self.parse_add()
        t = self.peek()
        if t.kind == "OP" and t.value in ("±", "+/-"):
            self.next()
            if self.peek().kind == "PCT":
                p = self.next()
                return PM(e, Num(p.value, p.line), True, t.line)
            return PM(e, self.parse_add(), False, t.line)
        if t.kind == "OP" and t.value == "..":
            self.next()
            return Between(e, self.parse_add(), t.line)
        return e

    def parse_add(self):
        e = self.parse_mul()
        while self.peek().kind == "OP" and self.peek().value in ("+", "-"):
            t = self.next()
            e = Bin(t.value, e, self.parse_mul(), t.line)
        return e

    def parse_mul(self):
        e = self.parse_unary()
        while self.peek().kind == "OP" and self.peek().value in ("*", "/", "//", "%"):
            t = self.next()
            e = Bin(t.value, e, self.parse_unary(), t.line)
        return e

    def parse_unary(self):
        t = self.peek()
        if t.kind == "OP" and t.value in ("-", "+"):
            self.next()
            return Unary(t.value, self.parse_unary(), t.line)
        return self.parse_pow()

    def parse_pow(self):
        base = self.parse_postfix()
        if self.is_op("**"):
            t = self.next()
            return Bin("**", base, self.parse_unary(), t.line)
        return base

    def parse_postfix(self):
        e = self.parse_primary()
        while True:
            t = self.peek()
            if t.kind != "OP":
                return e
            if t.value == "(":
                self.next()
                args, kwargs = self.parse_call_args()
                e = Call(e, args, kwargs, t.line)
            elif t.value == "[":
                self.next()
                idx = self.parse_expr()
                self.expect_op("]")
                e = Index(e, idx, t.line)
            elif t.value == ".":
                self.next()
                n = self.peek()
                if n.kind != "ID":
                    raise self.error(f"المفروض اسم حقل بعد النقطة، لقيت {_desc(n)}")
                self.next()
                e = Field(e, n.value, t.line)
            else:
                return e

    def parse_if(self):
        t = self.next()
        cond = self.parse_expr()
        then = self.parse_block()
        save, else_ = self.i, None
        self.skip_nl()
        if self.is_kw("else"):
            self.next()
            else_ = self.parse_if() if self.is_kw("if") else self.parse_block()
        else:
            self.i = save
        return If(cond, then, else_, t.line)

    def parse_primary(self):
        t = self.peek()
        k, v = t.kind, t.value
        if k == "NUM":
            self.next()
            return Num(v, t.line)
        if k == "STR":
            self.next()
            parts = []
            for p in v:
                if isinstance(p, str):
                    parts.append(p)
                else:
                    sub = Parser(p[1], p[2])
                    e = sub.parse_expr()
                    sub.skip_nl()
                    if sub.peek().kind != "EOF":
                        raise sub.error("تعبير زيادة جوه {} في النص")
                    parts.append(e)
            return Str(parts, t.line)
        if k == "ID":
            self.next()
            return Name(v, t.line)
        if k == "KW":
            if v in ("true", "false", "nil"):
                self.next()
                return Const({"true": True, "false": False, "nil": None}[v], t.line)
            if v == "if":
                return self.parse_if()
            if v == "fn":
                return FnExpr(self.parse_fn(False), t.line)
        if k == "OP":
            if v == "(":
                self.next()
                e = self.parse_expr()
                self.expect_op(")")
                return e
            if v == "[":
                self.next()
                items = []
                while not self.is_op("]"):
                    items.append(self.parse_expr())
                    if not self.accept_op(","):
                        break
                self.expect_op("]")
                return ListLit(items, t.line)
            if v == "{":
                return self.parse_record()
        if k == "PCT":
            raise self.error("% بعد الرقم بتتكتب بعد ± بس (مثال: 100 ± 5%)")
        raise self.error(f"تعبير ناقص: لقيت {_desc(t)}")

    def parse_record(self):
        t = self.next()
        pairs = []
        self.skip_nl()
        while not self.is_op("}"):
            kt = self.peek()
            if kt.kind == "ID":
                key = self.next().value
            elif kt.kind == "STR" and len(kt.value) == 1 and isinstance(kt.value[0], str):
                key = self.next().value[0]
            else:
                raise self.error(f"المفروض اسم حقل في السجل، لقيت {_desc(kt)}")
            self.expect_op(":", " بعد اسم الحقل")
            self.skip_nl()
            pairs.append((key, self.parse_expr()))
            self.skip_nl()
            if self.accept_op(","):
                self.skip_nl()
            elif not self.is_op("}"):
                raise self.error(f"المفروض , أو }} في السجل، لقيت {_desc(self.peek())}")
        self.next()
        return RecLit(pairs, t.line)


def e_line(node, default):
    return getattr(node, "line", default)


def parse(src):
    return Parser(src).parse_program()
