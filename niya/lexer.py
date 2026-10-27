"""lexer للغة نية: كلمات مفتاحية بالإنجليزي والعربي، أرقام هندية، نصوص بـ {interpolation}."""
import re
from collections import namedtuple

from .errors import ParseError

Tok = namedtuple("Tok", "kind value line pos end")   # kind: NUM PCT STR ID KW OP NL EOF

_DIAC = re.compile("[ً-ٰٟـ]")
_ALEF = str.maketrans("أإآ", "ااا")
_DIG = {c: str(i) for i, c in enumerate("٠١٢٣٤٥٦٧٨٩")}
_DIG.update({c: str(i) for i, c in enumerate("۰۱۲۳۴۵۶۷۸۹")})
_PUNCT = {"؟": "?", "،": ",", "؛": ";"}

KEYWORDS = {
    "let": "let", "ليكن": "let", "var": "var", "متغير": "var",
    "fn": "fn", "دالة": "fn",
    "if": "if", "لو": "if", "else": "else", "والا": "else",
    "while": "while", "طالما": "while", "for": "for", "لكل": "for", "in": "in", "في": "in",
    "return": "return", "ارجع": "return", "break": "break", "اكسر": "break",
    "continue": "continue", "كمل": "continue", "use": "use", "استخدم": "use",
    "require": "require", "يشترط": "require", "ensure": "ensure", "اضمن": "ensure",
    "example": "example", "مثال": "example",
    "true": "true", "صح": "true", "false": "false", "غلط": "false",
    "nil": "nil", "لاشيء": "nil", "لاشي": "nil", "عدم": "nil",
    "and": "and", "و": "and", "or": "or", "او": "or", "not": "not", "ليس": "not",
}
_OPS3 = ("+/-",)
_OPS2 = ("**", "//", "==", "!=", "<=", ">=", "->", "=>", "+=", "-=", "*=", "/=", "..")
_OPS1 = "+-*/%<>=()[]{},:;.?±|"
_PM = ("±", "+/-")
_CONT = {"+", "-", "*", "/", "//", "%", "**", "==", "!=", "<", ">", "<=", ">=", "=", "+=", "-=",
         "*=", "/=", ",", "(", "[", "{", ".", ":", "->", "=>", "..", "±", "+/-"}
_CONT_KW = {"and", "or", "not", "in"}
_CLOSE = {")": "(", "]": "[", "}": "{"}


def _isnum(c):
    return "0" <= c <= "9" or c in _DIG


def norm_ident(word):
    return _DIAC.sub("", word).translate(_ALEF)


def lex(src, line0=1):
    src = src.lstrip("﻿")
    toks, stack = [], []
    i, n, line = 0, len(src), line0

    def last():
        return toks[-1] if toks else None

    def err(msg, ln=None, eof=False):
        e = ParseError(msg, ln or line)
        e.eof = eof
        return e

    while i < n:
        c = src[i]
        if c == "\n":
            lt = last()
            skip = (
                (stack and stack[-1] in "([") or lt is None or lt.kind == "NL"
                or (lt.kind == "OP" and lt.value in _CONT) or (lt.kind == "KW" and lt.value in _CONT_KW)
            )
            if not skip:
                toks.append(Tok("NL", "\n", line, i, i + 1))
            line += 1
            i += 1
        elif c in " \t\r":
            i += 1
        elif c == "#":
            while i < n and src[i] != "\n":
                i += 1
        elif c in "\"'":
            start, start_line = i, line
            q, i = c, i + 1
            buf, parts = [], []
            while True:
                if i >= n:
                    raise err("نص مش مقفول", start_line, eof=True)
                ch = src[i]
                if ch == q:
                    i += 1
                    break
                if ch == "\\" and i + 1 < n:
                    buf.append({"n": "\n", "t": "\t"}.get(src[i + 1], src[i + 1]))
                    i += 2
                elif ch == "{":
                    depth, j = 1, i + 1
                    while j < n and depth:
                        if src[j] in "\"'":
                            k = src.find(src[j], j + 1)
                            j = k if k != -1 else n
                        elif src[j] == "{":
                            depth += 1
                        elif src[j] == "}":
                            depth -= 1
                        j += 1
                    if depth:
                        raise err("{ مش مقفولة جوه النص", line, eof=True)
                    if buf:
                        parts.append("".join(buf))
                        buf = []
                    parts.append(("expr", src[i + 1:j - 1], line))
                    i = j
                else:
                    if ch == "\n":
                        line += 1
                    buf.append(ch)
                    i += 1
            if buf or not parts:
                parts.append("".join(buf))
            toks.append(Tok("STR", parts, start_line, start, i))
        elif _isnum(c):
            j = i
            while j < n and _isnum(src[j]):
                j += 1
            if j + 1 < n and src[j] in ".٫" and _isnum(src[j + 1]):
                j += 1
                while j < n and _isnum(src[j]):
                    j += 1
            text = "".join(_DIG.get(ch, ch) for ch in src[i:j]).replace("٫", ".")
            val = float(text) if "." in text else int(text)
            lt = last()
            if lt and lt.kind == "OP" and lt.value in _PM and j < n and src[j] in "%٪":
                toks.append(Tok("PCT", val, line, i, j + 1))
                j += 1
            else:
                toks.append(Tok("NUM", val, line, i, j))
            i = j
        elif c.isalpha() or c == "_":
            j = i
            while j < n and (src[j].isalnum() or src[j] == "_" or "ً" <= src[j] <= "ٟ"
                             or src[j] in "ٰـ"):
                j += 1
            word = norm_ident(src[i:j]).translate(str.maketrans(_DIG))
            kw = KEYWORDS.get(word)
            toks.append(Tok("KW", kw, line, i, j) if kw else Tok("ID", word, line, i, j))
            i = j
        else:
            op = None
            for cand in _OPS3 + _OPS2:
                if src.startswith(cand, i):
                    op = cand
                    break
            if op is None:
                ch = _PUNCT.get(c, c)
                if ch in _OPS1:
                    op = ch
            if op is None:
                raise err(f"رمز مش مفهوم: {c!r}")
            if op in ("(", "[", "{"):
                stack.append(op)
            elif op in _CLOSE:
                if not stack or stack[-1] != _CLOSE[op]:
                    raise err(f"قوس {op} مالوش فتحة")
                stack.pop()
            toks.append(Tok("OP", op, line, i, i + len(op)))
            i += len(op)

    if stack:
        raise err(f"قوس {stack[-1]} مش مقفول", eof=True)
    if toks and toks[-1].kind != "NL":
        toks.append(Tok("NL", "\n", line, n, n))
    toks.append(Tok("EOF", None, line, n, n))
    return toks
