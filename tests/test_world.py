import socket
import struct
import threading
import time
import unittest

from niya import Interp, NiyaError
from niya import crypto, wire
from niya.values import Builtin, show_repr, Unc

QUOTE = '''
fn quote(area: number in 30..1000, floors: int in 1..10, kind: "house" | "apartment") -> number
  ensure result > 0
{ area * floors * 3500 }
'''


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Node:
    """بوابة شغّالة في thread بمفسّر خاص بيها (زي جهاز منفصل)."""

    def __init__(self, handlers="{}", manifest=True, peers="[]", store=False, name="g"):
        self.port, self.key = free_port(), wire.new_key()
        self.it = Interp(allow={"net"})
        self.ready, self.error = threading.Event(), None
        env = self.it.new_module_env()
        env.define("k", self.key)
        env.define("signal", Builtin("signal", lambda p: self.ready.set()))
        mf = ('let manifest = gate_manifest(k, {name: "%s", region: "luxor", tags: ["t"], addr: "127.0.0.1:%d"}, handlers)\n'
              % (name, self.port)) if manifest else "let manifest = nil\n"
        self.src = (QUOTE + f"let handlers = {handlers}\n" + mf +
                    f"gate_serve({{port: {self.port}, key: k, manifest: manifest, handlers: handlers, peers: {peers}, "
                    f"store: {str(store).lower()}, idle: 60, on_ready: fn(p) => signal(p)}})")
        self.env = env
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        try:
            self.it.run_source(self.src, self.env, "node.nia")
        except Exception as e:  # noqa: BLE001
            self.error = e
            self.ready.set()

    def start(self):
        self.thread.start()
        assert self.ready.wait(10) and self.error is None, self.error
        return self

    def stop(self):
        self.it.world._stop.set()
        self.thread.join(5)

    @property
    def addr(self):
        return f"127.0.0.1:{self.port}"


class Client:
    def __init__(self, allow=("net",)):
        self.it = Interp(allow=set(allow))
        self.env = self.it.new_module_env()
        self.it.fuel = 2_000_000

    def ev(self, src):
        return self.it.run_source(src, self.env, "client.nia")

    def raw(self, addr, msg, frame=True):
        host, port = addr.split(":")
        with socket.create_connection((host, int(port)), timeout=5) as s:
            if frame:
                wire.send_msg(s, msg)
            else:
                s.sendall(msg)
            try:
                return wire.recv_msg(s)
            except (ConnectionError, ValueError, OSError):
                return None


class WorldCase(unittest.TestCase):
    def node(self, **kw):
        n = Node(**kw).start()
        self.addCleanup(n.stop)
        return n


class TestCryptoAndWire(unittest.TestCase):
    VEC = [("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
            "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
            "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
           ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
            "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
            "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00")]

    def test_rfc8032_vectors(self):
        for sec, pub, msg, sig in self.VEC:
            s, m = bytes.fromhex(sec), bytes.fromhex(msg)
            self.assertEqual(crypto.public_key(s).hex(), pub)
            self.assertEqual(crypto.sign(s, m).hex(), sig)
            self.assertTrue(crypto.verify(bytes.fromhex(pub), m, bytes.fromhex(sig)))

    def test_tampering_is_detected(self):
        s = crypto.new_secret()
        pub, sig = crypto.public_key(s), crypto.sign(s, b"hello")
        self.assertTrue(crypto.verify(pub, b"hello", sig))
        self.assertFalse(crypto.verify(pub, b"hellO", sig))
        self.assertFalse(crypto.verify(pub, b"hello", sig[:-1] + bytes([sig[-1] ^ 1])))
        self.assertFalse(crypto.verify(crypto.public_key(crypto.new_secret()), b"hello", sig))
        self.assertFalse(crypto.verify(pub, b"hello", b"short"))

    def test_json_roundtrip_and_limits(self):
        v = {"a": [1, 2.5, "x", None, True], "u": Unc(90, 100, 110)}
        j = wire.to_json(v)
        self.assertEqual(j["u"], {"$unc": [90, 100, 110]})
        back = wire.from_json(j)
        self.assertEqual((back["u"].lo, back["u"].nom, back["u"].hi), (90, 100, 110))
        with self.assertRaises(NiyaError):
            wire.to_json({"f": Builtin("f", len)})                 # الدوال مبتتبعتش
        with self.assertRaises(NiyaError):
            wire.to_json({"$x": 1})                                # حقول محجوزة
        with self.assertRaises(NiyaError):
            wire.to_json(float("inf"))

    def test_content_id_is_canonical(self):
        self.assertEqual(wire.cid({"a": 1, "b": [1, 2]}), wire.cid({"b": [1, 2], "a": 1}))
        self.assertNotEqual(wire.cid({"a": 1}), wire.cid({"a": 2}))
        self.assertTrue(wire.cid("x").startswith("bab:blake2b:"))

    def test_secret_never_leaks(self):
        k = wire.new_key()
        self.assertNotIn(str(k["secret"]), show_repr(k))
        self.assertIn("<secret>", show_repr(k))
        with self.assertRaises(NiyaError):
            wire.to_json(k)
        c = Client(allow=())
        c.it.out = __import__("io").StringIO()
        c.ev('let k = key_new()\nprint(k)\nprint("سر: {k.secret}")')
        self.assertEqual(c.it.out.getvalue().count("<secret>"), 2)
        with self.assertRaises(NiyaError):
            c.ev("store_put(key_new())")

    def test_sign_verify_in_language(self):
        c = Client(allow=())
        self.assertTrue(c.ev('let k = key_new()\nlet e = sign(k, {x: 1})\nverify(e)'))
        self.assertFalse(c.ev('verify(set(e, "body", {x: 2}))'))
        self.assertFalse(c.ev('verify({body: 1})'))
        self.assertEqual(c.ev("e.from == k.id"), True)

    def test_local_store(self):
        c = Client(allow=())
        self.assertEqual(c.ev('let id = store_put({a: [1, 2]})\nstore_get(id)'), {"a": [1, 2]})
        self.assertIsNone(c.ev('store_get("bab:blake2b:00")'))

    def test_capabilities_denied_by_default(self):
        n_err = Client(allow=())
        with self.assertRaises(NiyaError) as cm:
            n_err.ev('gate_call("127.0.0.1:9", key_new(), "x", {})')
        self.assertIn("ممنوع", str(cm.exception))
        with self.assertRaises(NiyaError) as cm:
            n_err.ev('key_open("t.key")')
        self.assertIn("ممنوع", str(cm.exception))
        with self.assertRaises(NiyaError):
            Client(allow=("fs",)).ev('key_save(key_new(), "/tmp/notakey.txt")')   # لازم .key

    def test_schema_comes_from_the_signature(self):
        c = Client(allow=())
        s = c.ev(QUOTE + "gate_schema(quote)")
        self.assertEqual([p["name"] for p in s["takes"]], ["area", "floors", "kind"])
        self.assertEqual(s["takes"][0]["lo"], 30)
        self.assertEqual(s["takes"][2]["choices"], ["house", "apartment"])
        self.assertEqual((s["returns"], s["ensures"]), ("number", 1))


class TestGate(WorldCase):
    def setUp(self):
        self.g = self.node(handlers='{"quote.request": quote, "boom": fn(x) => x / 0}')
        self.c = Client()

    def call(self, cap, args, pin=True):
        pin_src = f'"{self.g.key["id"]}"' if pin else "nil"
        return self.c.ev(f'gate_call("{self.g.addr}", key_new(), "{cap}", {args}, {pin_src})')

    def test_call_ok_with_pinned_identity(self):
        r = self.call("quote.request", '{area: 100, floors: 2, kind: "house"}')
        self.assertEqual((r["status"], r["data"], r["from"], r["verified"]), ("ok", 700000, self.g.key["id"], True))

    def test_contract_refuses_bad_input_without_handwritten_checks(self):
        for args in ('{area: 5000, floors: 2, kind: "house"}', '{area: 100, floors: 2, kind: "villa"}',
                     '{area: 100, floors: 2.5, kind: "house"}'):
            r = self.call("quote.request", args)
            self.assertEqual(r["status"], "refused", args)
            self.assertIn("quote", r["msg"])

    def test_unknown_capability_and_crashing_handler(self):
        self.assertEqual(self.call("nope", "{}")["status"], "refused")
        r = self.call("boom", "{x: 1}")
        self.assertEqual(r["status"], "error")
        self.assertIn("قسمة على صفر", r["msg"])
        self.assertEqual(self.call("quote.request", '{area: 40, floors: 1, kind: "house"}')["status"], "ok")  # لسه شغّالة

    def test_wrong_pinned_identity_is_rejected_by_client(self):
        with self.assertRaises(NiyaError) as cm:
            self.c.ev(f'gate_call("{self.g.addr}", key_new(), "boom", {{x: 1}}, "{wire.ID_PREFIX + "00" * 32}")')
        self.assertIn("هوية", str(cm.exception))

    def test_find_returns_verified_manifest_with_schema(self):
        ms = self.c.ev(f'gate_find("{self.g.addr}", key_new(), {{cap: "quote.request", region: "luxor"}})')
        self.assertEqual(len(ms), 1)
        self.assertEqual(ms[0]["from"], self.g.key["id"])
        self.assertTrue(wire.check_signed(wire.to_json(ms[0])))
        caps = {c["cap"]: c for c in ms[0]["body"]["caps"]}
        self.assertEqual(caps["quote.request"]["takes"][2]["choices"], ["house", "apartment"])
        self.assertEqual(self.c.ev(f'gate_find("{self.g.addr}", key_new(), {{cap: "other"}})'), [])
        self.assertEqual(self.c.ev(f'gate_find("{self.g.addr}", key_new(), {{region: "cairo"}})'), [])


class TestAttacks(WorldCase):
    def setUp(self):
        self.g = self.node(handlers='{"quote.request": quote}', store=True)
        self.c = Client()
        self.me = wire.new_key()

    def msg(self, **over):
        m = {"v": 1, "verb": "CALL", "nonce": "abcdef0123456789", "time": time.time(), "cap": "quote.request",
             "args": {"area": 100, "floors": 1, "kind": "house"}}
        m.update(over)
        return m

    def send(self, m, key=None, sign=True):
        return self.c.raw(self.g.addr, wire.sign_fields(key or self.me, m) if sign else m)

    def test_valid_message_then_replay(self):
        m = wire.sign_fields(self.me, self.msg())
        self.assertEqual(self.c.raw(self.g.addr, m)["status"], "ok")
        again = self.c.raw(self.g.addr, m)
        self.assertEqual(again["status"], "refused")
        self.assertIn("nonce", again["msg"])

    def test_bad_signatures_and_formats(self):
        signed = wire.sign_fields(self.me, self.msg(nonce="aaaaaaaa11111111"))
        signed["args"] = {"area": 999, "floors": 1, "kind": "house"}        # اتعدّلت بعد التوقيع
        self.assertEqual(self.c.raw(self.g.addr, signed)["status"], "refused")
        self.assertEqual(self.send(self.msg(nonce="bbbbbbbb22222222"), sign=False)["status"], "refused")   # من غير توقيع
        forged = wire.sign_fields(self.me, self.msg(nonce="cccccccc33333333"))
        forged["from"] = wire.new_key()["id"]                               # انتحال هوية
        self.assertEqual(self.c.raw(self.g.addr, forged)["status"], "refused")
        self.assertEqual(self.send(self.msg(nonce="dddddddd44444444", v=2))["status"], "refused")
        self.assertEqual(self.send(self.msg(nonce="eeeeeeee55555555", verb="EVAL"))["status"], "refused")

    def test_stale_time_and_wrong_target(self):
        self.assertIn("وقت", self.send(self.msg(nonce="ffffffff66666666", time=time.time() - 3600))["msg"])
        self.assertIn("موجّهة", self.send(self.msg(nonce="aaaaaaaa77777777", to=wire.new_key()["id"]))["msg"])
        self.assertEqual(self.send(self.msg(nonce="aaaaaaaa88888888", to=self.g.key["id"]))["status"], "ok")

    def test_garbage_does_not_kill_the_server(self):
        self.c.raw(self.g.addr, struct.pack(">I", 5) + b"not{}", frame=False)           # JSON مش سليم
        self.c.raw(self.g.addr, struct.pack(">I", 2_000_000), frame=False)             # أكبر من الحد
        self.c.raw(self.g.addr, b"\x00", frame=False)                                  # رسالة ناقصة
        self.assertEqual(self.send(self.msg(nonce="aaaaaaaa99999999"))["status"], "ok")

    def test_executable_code_cannot_be_sent(self):
        r = self.send(self.msg(nonce="bbbbbbbb00000000", cap="__import__('os')"))
        self.assertEqual(r["status"], "refused")
        r = self.send(self.msg(nonce="bbbbbbbb00000001", args={"area": {"$unc": "x"}, "floors": 1, "kind": "house"}))
        self.assertEqual(r["status"], "refused")

    def test_content_roundtrip_and_poisoned_store(self):
        cid = self.c.ev(f'gate_put("{self.g.addr}", key_new(), {{price: 100 ± 10%, n: [1, 2]}})')
        got = self.c.ev(f'gate_get("{self.g.addr}", key_new(), "{cid}")')
        self.assertEqual((got["price"].lo, got["n"]), (90, [1, 2]))
        self.assertIsNone(self.c.ev(f'gate_get("{self.g.addr}", key_new(), "bab:blake2b:{"0" * 64}")'))
        fake = "bab:blake2b:" + "1" * 64
        self.g.it.world.store[fake] = {"evil": True}                                   # سيرفر بيرجّع محتوى غير مطابق
        with self.assertRaises(NiyaError) as cm:
            self.c.ev(f'gate_get("{self.g.addr}", key_new(), "{fake}")')
        self.assertIn("مش مطابق", str(cm.exception))

    def test_store_disabled_by_default(self):
        n = self.node()
        r = self.c.raw(n.addr, wire.sign_fields(self.me, self.msg(nonce="cccccccc00000000", verb="PUT",
                                                                  args={"value": 1}, cap=None)))
        self.assertEqual(r["status"], "refused")


class TestDirectory(WorldCase):
    def setUp(self):
        self.c = Client()

    def manifest(self, name, region="luxor"):
        return self.c.ev(f'let k_{name} = key_new()\n'
                         f'gate_manifest(k_{name}, {{name: "{name}", region: "{region}", tags: ["x"], addr: "h:1"}}, '
                         '{"quote.request": fn(a: number in 1..9) => a})')

    def test_announce_find_and_forgery(self):
        d = self.node(manifest=False)
        m1, m2 = self.manifest("one"), self.manifest("two", "cairo")
        self.c.env.define("m1", m1)
        self.c.env.define("m2", m2)
        self.assertEqual(self.c.ev(f'gate_announce("{d.addr}", key_new(), m1).status'), "ok")
        self.c.ev(f'gate_announce("{d.addr}", key_new(), m2)')
        found = self.c.ev(f'gate_find("{d.addr}", key_new(), {{cap: "quote.request", region: "luxor"}})')
        self.assertEqual([f["body"]["name"] for f in found], ["one"])
        self.assertEqual(len(self.c.ev(f'gate_find("{d.addr}", key_new(), {{cap: "quote.request"}})')), 2)
        # مانيفست متعدّل بعد التوقيع: الدليل لازم يرفضه (بنكلّمه بدون فحص العميل)
        bad = wire.to_json(m1)
        bad["body"]["name"] = "مزيّف"
        r = self.c.it.world.request(d.addr, wire.new_key(), "ANNOUNCE", {"args": {"manifest": bad, "ttl": 0}})
        self.assertEqual(r["status"], "refused")
        with self.assertRaises(NiyaError):                       # والعميل نفسه مبيبعتش مانيفست مش سليم
            self.c.ev('gate_announce("127.0.0.1:9", key_new(), set(m1, "body", {id: "x"}))')

    def test_deep_find_and_announce_relay(self):
        b = self.node(name="behind", handlers='{"quote.request": quote}')
        a = self.node(manifest=False, peers=f'["{b.addr}"]')
        found = self.c.ev(f'gate_find("{a.addr}", key_new(), {{cap: "quote.request"}}, true)')
        self.assertEqual([f["body"]["name"] for f in found], ["behind"])          # لقاها عن طريق الجار
        self.assertEqual(self.c.ev(f'gate_find("{a.addr}", key_new(), {{cap: "quote.request"}})'), [])   # من غير deep: لأ
        m = self.manifest("relayed")
        self.c.env.define("mr", m)
        self.c.ev(f'gate_announce("{a.addr}", key_new(), mr, 1)')
        for _ in range(60):                                       # التحويل بيتم في الخلفية
            if m["from"] in b.it.world.table:
                break
            time.sleep(0.05)
        self.assertIn(m["from"], b.it.world.table)


if __name__ == "__main__":
    unittest.main()
