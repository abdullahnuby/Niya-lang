"""عقدة العالم: سيرفر وعميل لبروتوكول BAB v0.1 على TCP.
كل رسالة موقّعة Ed25519 وفيها nonce ووقت. السيرفر بينفّذ قدرات (دوال نية) مسجّلة بس، ومبينفّذش أي كود جاي من برّه.
الرسائل: ANNOUNCE (إعلان بوابة) FIND (بحث) CALL (استدعاء قدرة) PUT/GET (محتوى ببصمته)."""
import os
import socket
import threading
import time

from . import nodes as nd
from . import wire
from .errors import ContractError, NiyaError
from .wire import canon_json, check_signed, from_json, recv_msg, send_msg, sign_fields, to_json

SKEW = 300.0
CALL_FUEL = 200_000
MAX_NONCES = 10_000
MAX_STORE = 10_000
MAX_TABLE = 1_000
VERBS = ("ANNOUNCE", "FIND", "CALL", "PUT", "GET")


def parse_addr(addr):
    host, sep, port = str(addr).rpartition(":")
    if not sep or not host or not port.isdigit() or not 0 < int(port) < 65536:
        raise NiyaError(f"عنوان غلط: {addr!r} (الشكل host:port)")
    return host, int(port)


def schema_of(c):
    """وصف قدرة من توقيع الدالة نفسها: المدخلات والأنواع والمدى والقيم المسموحة. ده بيان البوابة."""
    fd = c.fn

    def ty(t):
        if t is None:
            return {"type": "any"}
        d = {"type": t.base}
        if t.lo is not None:
            d["lo"], d["hi"] = t.lo, t.hi
        if t.choices is not None:
            d["choices"] = list(t.choices)
        return d

    takes = []
    for p in fd.params:
        d = {"name": p.name, **ty(p.type)}
        if p.default is not None:
            d["optional"] = True
        takes.append(d)
    count = lambda cls: sum(isinstance(x, cls) for x in fd.clauses)  # noqa: E731
    return {"takes": takes, "returns": ty(fd.ret)["type"] if fd.ret else "any",
            "ensures": count(nd.Ensure), "examples": count(nd.Example)}


def valid_manifest(env):
    """إعلان بوابة سليم: موقّع بهوية صاحبه، وbody.id = from، وشكله معقول."""
    try:
        if not check_signed(env) or len(canon_json(env)) > 65536:
            return False
        b = env.get("body")
        return (isinstance(b, dict) and b.get("id") == env["from"] and isinstance(b.get("caps", []), list)
                and all(isinstance(c, dict) and isinstance(c.get("cap"), str) for c in b.get("caps", []))
                and isinstance(b.get("tags", []), list) and all(isinstance(t, str) for t in b.get("tags", [])))
    except (TypeError, ValueError, AttributeError):
        return False


def _matches(env, q):
    b = env["body"]
    if "cap" in q and q["cap"] not in [c["cap"] for c in b.get("caps", [])]:
        return False
    if "region" in q and b.get("region") != q["region"]:
        return False
    return "tag" not in q or q["tag"] in b.get("tags", [])


class World:
    def __init__(self, it):
        self.it = it
        self.store, self.table, self.seen = {}, {}, {}
        self._stop = threading.Event()      # لإيقاف serve من برّه (الاختبارات)

    # ---------------------------------------------------------------- المخزن المحلي
    def put(self, value):
        j = to_json(value)
        c = wire.cid_of_json(j)
        self.store[c] = j
        return c

    def get(self, c):
        return from_json(self.store[c]) if c in self.store else None

    # ---------------------------------------------------------------- السيرفر
    def serve(self, key, port, handlers=None, manifest=None, host="127.0.0.1", max_requests=None,
              idle=30.0, peers=(), allow_store=False, on_ready=None, skew=SKEW):
        wire.check_key(key)
        if manifest is not None and (not valid_manifest(manifest) or manifest["from"] != key["id"]):
            raise NiyaError("المانيفست مش سليم أو مش موقّع بمفتاح البوابة دي")
        ctx = {"key": key, "handlers": handlers or {}, "manifest": manifest, "peers": list(peers),
               "allow_store": allow_store, "skew": skew}
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            srv.bind((host, port))
        except OSError as e:
            srv.close()
            raise NiyaError(f"مقدرتش أفتح {host}:{port}: {e.strerror}") from None
        srv.listen(16)
        srv.settimeout(0.25)
        if on_ready:
            on_ready(srv.getsockname()[1])
        served, last = 0, time.monotonic()
        try:
            while (not self._stop.is_set() and (max_requests is None or served < max_requests)
                   and time.monotonic() - last < idle):
                try:
                    conn, _ = srv.accept()
                except socket.timeout:
                    continue
                with conn:
                    conn.settimeout(5.0)
                    try:
                        self._handle(conn, ctx)
                    except (OSError, ValueError, UnicodeDecodeError, NiyaError):
                        pass
                served += 1
                last = time.monotonic()
        finally:
            srv.close()
        return served

    def _handle(self, conn, ctx):
        msg = recv_msg(conn)
        status, data, text = self._process(msg, ctx)
        reply = {"v": 1, "verb": "REPLY", "status": status, "time": time.time()}
        if isinstance(msg, dict) and isinstance(msg.get("nonce"), str):
            reply["to_nonce"] = msg["nonce"]
        if data is not None:
            reply["data"] = data
        if text:
            reply["msg"] = text
        send_msg(conn, sign_fields(ctx["key"], reply))

    def _process(self, msg, ctx):
        refused = lambda why: ("refused", None, why)  # noqa: E731
        if not isinstance(msg, dict) or msg.get("v") != 1 or msg.get("verb") not in VERBS:
            return refused("رسالة مش بصيغة BAB v1")
        if not check_signed(msg):
            return refused("التوقيع مش سليم أو ناقص")
        if msg.get("to") is not None and msg["to"] != ctx["key"]["id"]:
            return refused("الرسالة مش موجّهة للبوابة دي")
        t = msg.get("time")
        if not isinstance(t, (int, float)) or abs(time.time() - t) > ctx["skew"]:
            return refused("وقت الرسالة قديم أو بعيد (حماية من إعادة الإرسال)")
        nonce = msg.get("nonce")
        if not isinstance(nonce, str) or not 8 <= len(nonce) <= 64:
            return refused("nonce ناقص أو غلط")
        if nonce in self.seen:
            return refused("nonce اتستخدم قبل كده (إعادة إرسال)")
        self._remember(nonce, ctx["skew"])
        return getattr(self, "_v_" + msg["verb"].lower())(msg, ctx)

    def _remember(self, nonce, skew):
        now = time.time()
        self.seen[nonce] = now
        if len(self.seen) > MAX_NONCES:
            self.seen = {k: v for k, v in self.seen.items() if v > now - 2 * skew}
            if len(self.seen) > MAX_NONCES:
                self.seen = dict(sorted(self.seen.items(), key=lambda kv: kv[1])[MAX_NONCES // 2:])

    def _v_call(self, msg, ctx):
        cap = msg.get("cap")
        c = ctx["handlers"].get(cap) if isinstance(cap, str) else None
        if c is None:
            return "refused", None, f"مفيش قدرة اسمها {cap}"
        try:
            args = from_json(msg.get("args", {}))
        except NiyaError as e:
            return "refused", None, e.msg
        if not isinstance(args, dict):
            return "refused", None, "args لازم تكون سجل"
        self.it.fuel = CALL_FUEL
        try:
            return "ok", to_json(self.it.call_value(c, [], args, None)), None
        except ContractError as e:
            # مدخل أو شرط مسبق مخالف = غلطة الطالب. ensure/نوع الناتج = غلطة البوابة نفسها.
            return ("refused" if e.kind in ("input", "require") else "error"), None, e.msg
        except NiyaError as e:
            return "error", None, e.msg
        except Exception:  # noqa: BLE001  السيرفر ما يقعش بسبب طلب واحد
            return "error", None, "خطأ داخلي في البوابة"

    def _v_find(self, msg, ctx):
        q = msg.get("args", {})
        if not isinstance(q, dict):
            return "refused", None, "args لازم تكون سجل"
        pool = ([ctx["manifest"]] if ctx["manifest"] else []) + list(self.table.values())
        found = {e["from"]: e for e in pool if _matches(e, q)}
        if q.get("deep") is True:
            sub = {k: v for k, v in q.items() if k != "deep"}
            for peer in ctx["peers"]:
                try:
                    r = self.request(peer, ctx["key"], "FIND", {"args": sub}, timeout=2.0)
                except NiyaError:
                    continue
                for e in (r.get("data") or []) if r.get("status") == "ok" else []:
                    if valid_manifest(e) and _matches(e, q):
                        found.setdefault(e["from"], e)
        return "ok", list(found.values())[:50], None

    def _v_announce(self, msg, ctx):
        a = msg.get("args")
        env = a.get("manifest") if isinstance(a, dict) else None
        if not valid_manifest(env):
            return "refused", None, "المانيفست مش موقّع صح أو شكله غلط"
        ttl = a.get("ttl", 0)
        ttl = ttl if isinstance(ttl, int) and not isinstance(ttl, bool) else 0
        gid = env["from"]
        if gid not in self.table and len(self.table) >= MAX_TABLE:
            return "refused", None, "جدول البوابات امتلأ"
        fresh = self.table.get(gid) != env
        self.table[gid] = env
        if fresh and ttl > 0:
            for peer in ctx["peers"]:
                threading.Thread(target=self._forward, args=(peer, ctx["key"], env, min(ttl, 3) - 1),
                                 daemon=True).start()
        return "ok", {"stored": True}, None

    def _forward(self, peer, key, env, ttl):
        try:
            self.request(peer, key, "ANNOUNCE", {"args": {"manifest": env, "ttl": ttl}}, timeout=2.0)
        except NiyaError:
            pass

    def _v_put(self, msg, ctx):
        if not ctx["allow_store"]:
            return "refused", None, "البوابة دي مش بتخزّن محتوى"
        a = msg.get("args")
        if not isinstance(a, dict) or "value" not in a:
            return "refused", None, "args.value ناقصة"
        try:
            j = to_json(from_json(a["value"]))
        except NiyaError as e:
            return "refused", None, e.msg
        c = wire.cid_of_json(j)
        if c not in self.store and len(self.store) >= MAX_STORE:
            return "refused", None, "المخزن امتلأ"
        self.store[c] = j
        return "ok", {"cid": c}, None

    def _v_get(self, msg, ctx):
        a = msg.get("args")
        c = a.get("cid") if isinstance(a, dict) else None
        if not isinstance(c, str) or c not in self.store:
            return "error", None, "المحتوى ده مش موجود هنا"
        return "ok", self.store[c], None

    # ---------------------------------------------------------------- العميل
    def request(self, addr, key, verb, fields, timeout=5.0, expect=None):
        host, port = parse_addr(addr)
        msg = {"v": 1, "verb": verb, "nonce": os.urandom(8).hex(), "time": time.time(), **fields}
        if expect:
            msg["to"] = expect
        signed = sign_fields(key, msg)
        try:
            with socket.create_connection((host, port), timeout=timeout) as s:
                s.settimeout(timeout)
                send_msg(s, signed)
                reply = recv_msg(s)
        except (OSError, ValueError) as e:
            raise NiyaError(f"مقدرتش أكلّم {addr}: {e}") from None
        if not (isinstance(reply, dict) and reply.get("verb") == "REPLY" and check_signed(reply)):
            raise NiyaError("رد البوابة مش موقّع صح")
        if reply.get("to_nonce") != msg["nonce"]:
            raise NiyaError("الرد مش بتاع الطلب ده (nonce مختلف)")
        if expect and reply["from"] != expect:
            raise NiyaError("هوية البوابة اللي ردت مختلفة عن المتوقعة (ممكن تكون بوابة مزيّفة)")
        t = reply.get("time")
        if not isinstance(t, (int, float)) or abs(time.time() - t) > SKEW:
            raise NiyaError("وقت الرد بعيد عن وقتك")
        return reply
