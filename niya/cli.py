import argparse
import os
import sys
import threading

from . import __version__
from .check import check_file
from .errors import NiyaError, ParseError
from .interp import Interp
from .values import show_repr

_STATUS = {"verified": "متحقق ✓", "failed": "فاشل ✗", "hole": "ناقص: جسمها { ? } ⚠"}
_ARDIG = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def fmt_err(e, default_file=None):
    f = getattr(e, "file", None) or default_file
    where = f"[{os.path.basename(f)}] " if f and not str(f).startswith("<") else ""
    return f"خطأ {where}{e}"


def parse_cli_value(s):
    t = s.translate(_ARDIG)
    for conv in (int, float):
        try:
            return conv(t)
        except ValueError:
            pass
    return {"true": True, "false": False, "nil": None}.get(s, s)


def _allow(spec):
    got = {x.strip() for x in (spec or "").split(",") if x.strip()}
    bad = got - {"net", "fs", "all"}
    if bad:
        raise NiyaError(f"صلاحية مش معروفة: {', '.join(sorted(bad))} (المتاح: net, fs, all)")
    return got


def cmd_run(a):
    it = Interp(fuel=a.fuel, allow=_allow(a.allow))
    it.run_file(a.file)
    return 0


def cmd_check(a):
    reports, out = check_file(a.file, a.seed, a.fuzz)
    if not reports:
        print("مفيش دوال فيها عقود (require/ensure/example) أو أنواع عشان تتفحص.")
        return 0
    for r in reports:
        print(f"fn {r.name}  (سطر {r.line})")
        for mark, text in r.lines:
            print(f"  {mark} {text}")
        print(f"  ⇒ {_STATUS[r.status]}" + (f"   ختم: {r.seal}" if r.seal else "") + "\n")
    if any(r.status == "failed" for r in reports):
        return 1
    return 2 if any(r.status == "hole" for r in reports) else 0


def cmd_call(a):
    it = Interp(fuel=a.fuel, allow=_allow(a.allow))
    env = it.run_file(a.file)
    cell = env.lookup(a.fn)
    if cell is None:
        print(f"مفيش دالة اسمها {a.fn} في الملف")
        return 1
    args, kwargs = [], {}
    for s in a.args:
        if "=" in s and s.split("=", 1)[0].isidentifier():
            k, v = s.split("=", 1)
            kwargs[k] = parse_cli_value(v)
        else:
            args.append(parse_cli_value(s))
    res = it.call_value(cell[0], args, kwargs)
    if res is not None:
        print(show_repr(res))
    return 0


def cmd_repl(a):
    it = Interp(allow=_allow(a.allow))
    it.repl = True
    env = it.new_module_env()
    print(f"نية {__version__}  (اكتب exit() أو Ctrl-D للخروج)")
    buf = ""
    while True:
        try:
            line = input("...  " if buf else "نية> ")
        except EOFError:
            print()
            return 0
        if not buf and line.strip() in ("exit()", "exit", "خروج"):
            return 0
        buf += line + "\n"
        if not buf.strip():
            buf = ""
            continue
        it.fuel = 5_000_000
        try:
            val = it.run_source(buf, env, "<repl>")
        except ParseError as e:
            if e.eof:
                continue
            print(fmt_err(e))
            buf = ""
            continue
        except NiyaError as e:
            print(fmt_err(e))
            buf = ""
            continue
        buf = ""
        if val is not None:
            print(show_repr(val))


def _main(argv):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(prog="niya", description="نية: لغة برمجة بعقود وأرقام غير يقينية")
    ap.add_argument("--version", action="version", version=f"niya {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="شغّل ملف .nia")
    r.add_argument("file")
    r.add_argument("--fuel", type=int, default=5_000_000, help="حد الخطوات (يمنع الحلقات اللانهائية)")
    r.add_argument("--allow", default="", help="صلاحيات: net (شبكة) fs (ملفات المفاتيح) all. الافتراضي: ولا واحدة")
    c = sub.add_parser("check", help="اتحقق من العقود: example + ensure/require بحالات حدود وعشوائية")
    c.add_argument("file")
    c.add_argument("--seed", type=int, default=0)
    c.add_argument("--fuzz", type=int, default=300)
    k = sub.add_parser("call", help="نادي دالة من ملف: niya call f.nia اسم_الدالة 180 2 kind=house")
    k.add_argument("file")
    k.add_argument("fn")
    k.add_argument("args", nargs="*")
    k.add_argument("--fuel", type=int, default=5_000_000)
    k.add_argument("--allow", default="", help="صلاحيات: net, fs, all")
    rp = sub.add_parser("repl", help="سطر أوامر تفاعلي")
    rp.add_argument("--allow", default="", help="صلاحيات: net, fs, all")
    a = ap.parse_args(argv)
    path = getattr(a, "file", None)
    try:
        return {"run": cmd_run, "check": cmd_check, "call": cmd_call, "repl": cmd_repl}[a.cmd](a)
    except NiyaError as e:
        print(fmt_err(e, path))
        return 1
    except RecursionError:
        print("خطأ: العمق زاد (recursion)")
        return 1
    except OSError as e:
        print(f"مقدرتش أقرا الملف: {e}")
        return 1


def main(argv=None):
    sys.setrecursionlimit(30000)
    res = [1]

    def target():
        try:
            res[0] = _main(argv)
        except SystemExit as e:          # argparse (--version / -h / خطأ في الأوامر) بيطلع بـ SystemExit
            res[0] = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)

    for size in (256, 64, 0):
        try:
            threading.stack_size(size * 1024 * 1024)
            break
        except (ValueError, RuntimeError):
            continue
    t = threading.Thread(target=target)
    t.start()
    t.join()
    return res[0]
