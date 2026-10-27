from collections import namedtuple


def N(name, fields):
    return namedtuple(name, (fields + " line").split())


# تعبيرات
Num = N("Num", "value")
Str = N("Str", "parts")                  # parts: نص أو عقدة تعبير
Const = N("Const", "value")
Name = N("Name", "name")
ListLit = N("ListLit", "items")
RecLit = N("RecLit", "pairs")
Unary = N("Unary", "op operand")
Bin = N("Bin", "op left right")
Logic = N("Logic", "op left right")
Not = N("Not", "operand")
Cmp = N("Cmp", "first rest")             # rest: [(op, node)]
PM = N("PM", "base amount percent")      # base ± amount
Between = N("Between", "lo hi")          # lo .. hi
Call = N("Call", "fn args kwargs")
Index = N("Index", "obj idx")
Field = N("Field", "obj name")
If = N("If", "cond then else_")
FnExpr = N("FnExpr", "fn")
Block = N("Block", "stmts")

# جمل
Let = N("Let", "name expr mutable")
Assign = N("Assign", "name op expr")
Return = N("Return", "expr")
Break = N("Break", "")
Continue = N("Continue", "")
While = N("While", "cond body")
For = N("For", "names iter body")
Use = N("Use", "path")
ExprStmt = N("ExprStmt", "expr")
DefFn = N("DefFn", "fn")

# بنود العقد
Require = N("Require", "expr src")
Ensure = N("Ensure", "expr src")
Example = N("Example", "args kwargs expected src")


class TypeSpec:
    def __init__(self, base, lo=None, hi=None, choices=None):
        self.base, self.lo, self.hi, self.choices = base, lo, hi, choices


class Param:
    def __init__(self, name, type, default, line):
        self.name, self.type, self.default, self.line = name, type, default, line


class FnDef:
    def __init__(self, name, params, clauses, body, ret, text, line, hole):
        self.name, self.params, self.clauses = name, params, clauses
        self.body, self.ret, self.text, self.line, self.hole = body, ret, text, line, hole
