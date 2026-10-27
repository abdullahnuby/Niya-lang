import io
import os
import pathlib
import sys
import tempfile
import unittest

from niya import ContractError, FuelError, HoleError, Interp, NiyaError, ParseError
from niya.check import check_file

EX = pathlib.Path(__file__).resolve().parent.parent / "examples"


def setUpModule():
    sys.setrecursionlimit(30000)


def run(src, fuel=1_000_000):
    out = io.StringIO()
    it = Interp(fuel=fuel, out=out)
    it.run_source(src, it.new_module_env(), "t.nia")
    return out.getvalue().strip()


class TestCore(unittest.TestCase):
    def test_arithmetic_and_precedence(self):
        self.assertEqual(run("print(2 + 3 * 4, (2 + 3) * 4, 2 ** 3 ** 2, -2 ** 2, 7 // 2, 7 % 3)"),
                         "14 20 512 -4 3 1")

    def test_strings_and_interpolation(self):
        self.assertEqual(run('let n = "ع"\nprint("a{n}b {1 + 1}", "x" + "y")'), "aعb 2 xy")

    def test_arabic_keywords_and_digits(self):
        src = 'دالة ضعف(س) => س * ٢\nليكن ق = [١, ٢, ٣]\nمتغير م = ٠\nلكل ع في ق { م += ضعف(ع) }\nاطبع(م)'
        self.assertEqual(run(src), "12")

    def test_closures_recursion_higher_order(self):
        src = """
fn adder(n) => fn(x) => x + n
let add5 = adder(5)
fn fact(n) => if n <= 1 { 1 } else { n * fact(n - 1) }
print(add5(10), fact(10), map([1, 2, 3], add5), reduce([1, 2, 3, 4], fn(a, b) => a * b, 1))
"""
        self.assertEqual(run(src), "15 3628800 [6, 7, 8] 24")

    def test_loops_break_continue(self):
        src = """
var s = 0
var i = 0
while true {
  i += 1
  if i > 10 { break }
  if i % 2 == 0 { continue }
  s += i
}
print(s)
"""
        self.assertEqual(run(src), "25")

    def test_records_lists_are_immutable_values(self):
        src = 'let a = [1, 2]\nlet b = push(a, 3)\nlet r = {x: 1}\nlet r2 = set(r, "x", 9)\nprint(a, b, r.x, r2.x)'
        self.assertEqual(run(src), "[1, 2] [1, 2, 3] 1 9")

    def test_named_args_and_defaults(self):
        self.assertEqual(run("fn f(a, b = 10) => a - b\nprint(f(b: 1, a: 5), f(5))"), "4 -5")

    def test_use_module_and_cycle(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d)
            (p / "lib.nia").write_text("fn double(x) => x * 2\nlet _hidden = 1\n", encoding="utf-8")
            (p / "main.nia").write_text('use "lib"\nprint(double(21))\n', encoding="utf-8")
            (p / "a.nia").write_text('use "b.nia"\n', encoding="utf-8")
            (p / "b.nia").write_text('use "a.nia"\n', encoding="utf-8")
            out = io.StringIO()
            Interp(out=out).run_file(str(p / "main.nia"))
            self.assertEqual(out.getvalue().strip(), "42")
            with self.assertRaises(NiyaError) as cm:
                Interp().run_file(str(p / "a.nia"))
            self.assertIn("دايرية", cm.exception.msg)


class TestSafety(unittest.TestCase):
    def assertFails(self, src, text, exc=NiyaError, fuel=1_000_000):
        with self.assertRaises(exc) as cm:
            run(src, fuel)
        self.assertIn(text, str(cm.exception))

    def test_let_is_immutable(self):
        self.assertFails("let x = 1\nx = 2", "متعرّف بـ let")
        self.assertFails("let x = 1\nlet x = 2", "اتعرّف قبل كده")

    def test_conditions_must_be_bool(self):
        self.assertFails("if 1 { 2 }", "صح/غلط")
        self.assertFails("print(1 and true)", "صح/غلط")

    def test_no_python_internals(self):
        self.assertFails('print("".__class__)', "مفيهوش حقل")
        self.assertFails("print(__import__)", "اسم مش معروف")

    def test_infinite_loop_is_stopped(self):
        self.assertFails("while true { }", "خطوات", FuelError, fuel=50_000)

    def test_deep_recursion_is_an_error_not_a_crash(self):
        self.assertFails("fn f(n) => f(n + 1)\nf(0)", "recursion", NiyaError, fuel=5_000_000)

    def test_type_mixing_is_an_error(self):
        self.assertFails('print(1 + "a")', "مش ممكن تجمع")
        self.assertFails("print(1 / 0)", "قسمة على صفر")
        self.assertFails("print([1][5])", "برّه الحدود")

    def test_parse_errors_have_lines(self):
        with self.assertRaises(ParseError) as cm:
            run("let a = 1\nlet b = (2 +\n")
        self.assertTrue(cm.exception.eof)
        with self.assertRaises(ParseError) as cm:
            run("let a = 1\nlet 5 = 2")
        self.assertEqual(cm.exception.line, 2)


class TestUncertainty(unittest.TestCase):
    def test_interval_arithmetic(self):
        self.assertEqual(run("let p = 100 ± 10%\nprint(p * 2, p + 5, p.low, p.high, p.mid)"),
                         "200 [180 .. 220] 105 [95 .. 115] 90 110 100")

    def test_undecided_comparison_is_refused(self):
        with self.assertRaises(NiyaError) as cm:
            run("print(100 ± 10% < 105)")
        self.assertIn("مش حاسمة", cm.exception.msg)

    def test_decided_comparison_works(self):
        self.assertEqual(run("print(100 ± 10% < 200, 100 ± 10% > 200, (100 ± 10%).high <= 110)"), "true false true")

    def test_exact_result_collapses_to_number(self):
        self.assertEqual(run("print(100 ± 0%)"), "100")

    def test_between_and_ascii_spelling(self):
        self.assertEqual(run("print(8..16, 50 +/- 2)"), "12 [8 .. 16] 50 [48 .. 52]")


class TestContracts(unittest.TestCase):
    SRC = """
fn f(x: int in 1..10, kind: "a" | "b" = "a") -> int
  require x != 5
  ensure result >= x
{
  x * 2
}
"""

    def call(self, expr):
        return run(self.SRC + f"print({expr})")

    def test_ok(self):
        self.assertEqual(self.call('f(3)'), "6")

    def test_input_range_enum_and_integrality(self):
        for bad, kind in (("f(0)", "input"), ('f(3, "z")', "input"), ("f(2.5)", "input")):
            with self.assertRaises(ContractError) as cm:
                self.call(bad)
            self.assertEqual(cm.exception.kind, kind, bad)

    def test_require_and_ensure(self):
        with self.assertRaises(ContractError) as cm:
            self.call("f(5)")
        self.assertEqual(cm.exception.kind, "require")
        with self.assertRaises(ContractError) as cm:
            run("fn g(x) -> number\n  ensure result > x\n{ x - 1 }\ng(3)")
        self.assertEqual(cm.exception.kind, "ensure")

    def test_return_type(self):
        with self.assertRaises(ContractError) as cm:
            run('fn g() -> int { "a" }\ng()')
        self.assertEqual(cm.exception.kind, "return")

    def test_hole(self):
        with self.assertRaises(HoleError):
            run("fn g(x) { ? }\ng(1)")


class TestChecker(unittest.TestCase):
    def reports(self, name, **kw):
        reps, _ = check_file(str(EX / name), **kw)
        return {r.name: r for r in reps}

    def test_good_examples_verify(self):
        for f, names in (("quote.nia", ["quote", "duration_days"]), ("boq.nia", ["boq_total", "verdict"]),
                         ("quote_ar.nia", ["مقايسة"]), ("bab_world.nia", ["find"])):
            r = self.reports(f)
            self.assertEqual(sorted(r), sorted(names), f)
            for x in r.values():
                self.assertEqual(x.status, "verified", f"{f}:{x.name} {x.lines}")
                self.assertTrue(x.seal.startswith("@blake2b:"))

    def test_broken_is_caught_both_ways(self):
        r = self.reports("broken.nia")
        text = "\n".join(t for _, t in r["duration_days"].lines)
        self.assertEqual(r["duration_days"].status, "failed")
        self.assertIn("المتوقع 44 وطلع", text)          # من الـ example
        self.assertIn("مثال معاكس", text)                # من الفحص العشوائي
        self.assertEqual(r["ratio"].status, "failed")
        self.assertIn("قسمة على صفر", "\n".join(t for _, t in r["ratio"].lines))

    def test_hole_is_not_faked(self):
        r = self.reports("hole.nia")["price"]
        self.assertEqual(r.status, "hole")
        self.assertIsNone(r.seal)

    def test_deterministic(self):
        a = [x.lines for x in check_file(str(EX / "broken.nia"), seed=3)[0]]
        b = [x.lines for x in check_file(str(EX / "broken.nia"), seed=3)[0]]
        self.assertEqual(a, b)

    def test_top_level_output_is_swallowed(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "t.nia")
            with open(p, "w", encoding="utf-8") as f:
                f.write('print("noise")\nfn f(x: int in 1..5) -> int\n  ensure result > 0\n{ x }\n')
            reps, out = check_file(p)
            self.assertEqual(out.strip(), "noise")
            self.assertEqual(reps[0].status, "verified")


if __name__ == "__main__":
    unittest.main()
