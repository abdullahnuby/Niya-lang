"""عالم نية على جهازين (عمليات) حقيقيين: دليل + بوابتين + عميل، كلهم بيتكلموا TCP بتوقيعات Ed25519.
التشغيل (من فولدر المشروع):  python examples/world/demo.py
كل عملية بتشتغل في فولدر مؤقت (مفاتيحها هناك)، وبتتقفل لما العرض يخلص."""
import os
import pathlib
import socket
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
ENV = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start(wd, file, *args):
    cmd = [sys.executable, "-m", "niya", "call", str(HERE / file), *args, "--allow", "net,fs"]
    p = subprocess.Popen(cmd, cwd=wd, env=ENV, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8")
    lines = []
    while True:                                   # استنى سطر "شغّال" (بيتطبع لما السيرفر يبدأ يسمع)
        line = p.stdout.readline()
        if not line:
            raise SystemExit(f"{file} وقفت قبل ما تشتغل:\n" + "".join(lines))
        lines.append(line)
        if "على" in line and "هويت" in line:
            return p, "".join(lines)


def run(wd, file, fn, *args):
    cmd = [sys.executable, "-m", "niya", "call", str(HERE / file), fn, *args, "--allow", "net"]
    r = subprocess.run(cmd, cwd=wd, env=ENV, capture_output=True, text=True, encoding="utf-8", timeout=60)
    return (r.stdout + r.stderr).rstrip(), r.returncode


def main():
    try:                                          # كونسول ويندوز بيبوّظ العربي من غير ده
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
    dp, ap, bp = free_port(), free_port(), free_port()
    directory = f"127.0.0.1:{dp}"
    procs = []
    with tempfile.TemporaryDirectory() as wd:
        try:
            print("== 1) الدليل والبوابتين بيشتغلوا (3 عمليات منفصلة) ==")
            for file, args in (("directory.nia", ("main", str(dp))),
                               ("gate_mazaya.nia", ("main", str(ap), f"directory={directory}")),
                               ("gate_rival.nia", ("main", str(bp), f"directory={directory}"))):
                p, out = start(wd, file, *args)
                procs.append(p)
                print(out.rstrip())
            print("\n== 2) عميل بيدوّر بالنية (القدرة + المنطقة) ويكلّم اللي لقاهم ==")
            print(run(wd, "client.nia", "main", directory)[0])
            print("\n== 3) طلب غلط عمداً (مساحة 5000): الرفض من عقد كل بوابة ==")
            print(run(wd, "client.nia", "probe", directory)[0])
            print("\n== 4) بوابة بهوية مش اللي أنا مستنيها: العميل لازم يرفض ==")
            out, code = run(wd, "client.nia", "impostor", directory)
            print(out)
            print(f"(كود الخروج {code}:", "اترفضت صح)" if code != 0 and "مفروض" not in out else "!! ماتمسكتش)")
        finally:
            for p in procs:
                p.terminate()
                p.wait(timeout=10)


if __name__ == "__main__":
    main()
