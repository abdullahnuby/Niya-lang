class NiyaError(Exception):
    """أي خطأ في لغة نية (صياغة أو تشغيل)."""

    def __init__(self, msg, line=None, file=None):
        super().__init__(msg)
        self.msg = msg
        self.line = line
        self.file = file

    def __str__(self):
        return (f"سطر {self.line}: " if self.line else "") + self.msg


class ParseError(NiyaError):
    eof = False


class ContractError(NiyaError):
    """kind: input | require | ensure | return"""

    def __init__(self, msg, line=None, kind="ensure", fn=None):
        super().__init__(msg, line)
        self.kind = kind
        self.fn = fn


class HoleError(NiyaError):
    """دالة جسمها { ? } لسه فاضية."""


class FuelError(NiyaError):
    """الكود خد خطوات أكتر من المسموح (غالباً حلقة لا نهائية)."""
