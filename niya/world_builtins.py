"""دوال العالم في اللغة: هوية وتوقيع وبصمة ومحتوى وبوابات. الشبكة والملفات ممنوعة افتراضياً (--allow net,fs)."""
import json
import os

from . import wire
from .errors import NiyaError
from .values import Closure, Secret
from .wire import from_json, to_json
from .world import World, schema_of, valid_manifest

SERVE_KEYS = {"port", "key", "handlers", "manifest", "host", "max", "idle", "peers", "store", "on_ready"}


def _need_record(v, what):
    if not isinstance(v, dict):
        raise NiyaError(f"{what} لازم يكون سجل")
    return v


def _key_path(path):
    if not isinstance(path, str) or not path.endswith(".key"):
        raise NiyaError("ملف المفتاح لازم ينتهي بـ .key (حماية من الكتابة فوق ملفات تانية)")
    return path


def install_world(it, reg):
    it.world = W = World(it)

    def key_save(key, path):
        it.need_cap("fs")
        wire.check_key(key)
        fd = os.open(_key_path(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"id": key["id"], "secret": str(key["secret"])}, f)
        return key["id"]

    def key_load(path):
        it.need_cap("fs")
        try:
            with open(_key_path(path), encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError) as e:
            raise NiyaError(f"مقدرتش أقرا المفتاح {path}: {e}") from None
        if not isinstance(d, dict):
            raise NiyaError("ملف المفتاح مش سليم")
        key = {"id": d.get("id"), "secret": Secret(str(d.get("secret")))}
        wire.check_key(key)
        return key

    def key_open(path):
        it.need_cap("fs")
        if os.path.exists(_key_path(path)):
            return key_load(path)
        key = wire.new_key()
        key_save(key, path)
        return key

    def sign(key, body):
        return from_json(wire.sign_fields(key, {"body": to_json(body)}))

    def verify(env):
        try:
            return wire.check_signed(to_json(env))
        except NiyaError:
            return False

    def gate_schema(f):
        if not isinstance(f, Closure):
            raise NiyaError("gate_schema بتاخد دالة نية")
        return from_json(schema_of(f))

    def gate_manifest(key, info, handlers):
        wire.check_key(key)
        j = to_json(_need_record(info, "info"))
        if "id" in j or "caps" in j:
            raise NiyaError("info مينفعش فيها id أو caps (بتتحسب لوحدها)")
        caps = []
        for name, c in _need_record(handlers, "handlers").items():
            if not isinstance(c, Closure):
                raise NiyaError(f"القدرة {name} لازم تكون دالة نية")
            caps.append({"cap": name, **schema_of(c)})
        return from_json(wire.sign_fields(key, {"body": {**j, "id": key["id"], "caps": caps}}))

    def gate_serve(cfg):
        it.need_cap("net")
        _need_record(cfg, "الإعدادات")
        extra = set(cfg) - SERVE_KEYS
        if extra:
            raise NiyaError(f"إعداد مش معروف: {', '.join(sorted(extra))} (المتاح: {', '.join(sorted(SERVE_KEYS))})")
        for need in ("port", "key"):
            if need not in cfg:
                raise NiyaError(f"gate_serve ناقصها {need}")
        handlers = _need_record(cfg.get("handlers", {}), "handlers")
        if not all(isinstance(c, Closure) for c in handlers.values()):
            raise NiyaError("كل القدرات في handlers لازم تكون دوال نية")
        hook = cfg.get("on_ready")
        saved = it.fuel
        try:
            return W.serve(
                cfg["key"], cfg["port"], handlers,
                manifest=to_json(cfg["manifest"]) if cfg.get("manifest") is not None else None,
                host=cfg.get("host", "127.0.0.1"), max_requests=cfg.get("max"),
                idle=float(cfg.get("idle", 30)), peers=cfg.get("peers", []),
                allow_store=bool(cfg.get("store", False)),
                on_ready=(lambda p: it.call_value(hook, [p])) if hook is not None else None)
        finally:
            it.fuel = saved

    def _result(reply):
        return {"status": reply.get("status", "error"),
                "data": from_json(reply["data"]) if "data" in reply else None,
                "msg": reply.get("msg"), "from": reply["from"], "verified": True}

    def gate_call(addr, key, cap, args, expect=None, timeout=5):
        it.need_cap("net")
        return _result(W.request(addr, key, "CALL", {"cap": cap, "args": to_json(_need_record(args, "args"))},
                                 timeout=float(timeout), expect=expect))

    def gate_find(addr, key, query, deep=False):
        it.need_cap("net")
        q = to_json(_need_record(query, "query"))
        if deep:
            q["deep"] = True
        r = W.request(addr, key, "FIND", {"args": q})
        if r.get("status") != "ok":
            raise NiyaError(f"FIND اترفض: {r.get('msg')}")
        return [from_json(e) for e in r.get("data", []) if valid_manifest(e)]

    def gate_announce(addr, key, manifest, ttl=1):
        it.need_cap("net")
        env = to_json(manifest)
        if not valid_manifest(env):
            raise NiyaError("المانيفست مش موقّع صح (استخدم gate_manifest)")
        return _result(W.request(addr, key, "ANNOUNCE", {"args": {"manifest": env, "ttl": int(ttl)}}))

    def gate_put(addr, key, value):
        it.need_cap("net")
        r = W.request(addr, key, "PUT", {"args": {"value": to_json(value)}})
        if r.get("status") != "ok":
            raise NiyaError(f"PUT اترفض: {r.get('msg')}")
        if r["data"].get("cid") != wire.cid(value):
            raise NiyaError("البوابة رجّعت بصمة مش مطابقة للمحتوى اللي بعتّه")
        return r["data"]["cid"]

    def gate_get(addr, key, cid):
        it.need_cap("net")
        r = W.request(addr, key, "GET", {"args": {"cid": cid}})
        if r.get("status") != "ok":
            return None
        if wire.cid_of_json(r["data"]) != cid:
            raise NiyaError("المحتوى اللي رجع مش مطابق للبصمة (تزوير أو تلف)")
        return from_json(r["data"])

    reg("key_new", lambda: wire.new_key())
    reg("key_save", key_save)
    reg("key_load", key_load)
    reg("key_open", key_open)
    reg("sign", sign)
    reg("verify", verify)
    reg("cid", wire.cid)
    reg("store_put", W.put)
    reg("store_get", W.get)
    reg("gate_schema", gate_schema)
    reg("gate_manifest", gate_manifest)
    reg("gate_serve", gate_serve)
    reg("gate_call", gate_call)
    reg("gate_find", gate_find)
    reg("gate_announce", gate_announce)
    reg("gate_put", gate_put)
    reg("gate_get", gate_get)
