"""Akış bazlı indirme bellekteki 2 MB sınırını aşmamalıdır."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mailbul


class _SahteYanit:
    def __init__(self, parcalar, content_length=None):
        self.parcalar = parcalar
        self.headers = {}
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)
        self.kapatildi = False

    def iter_content(self, chunk_size):
        yield from self.parcalar

    def close(self):
        self.kapatildi = True


class _SahteOturum:
    def __init__(self, yanit):
        self.yanit = yanit

    def get(self, *args, **kwargs):
        return self.yanit


def test_safe_get_content_length_olmasa_da_2mb_sinirini_uygular(monkeypatch):
    limit = 2 * 1024 * 1024
    yanit = _SahteYanit([b"a" * limit, b"b"])
    monkeypatch.setattr(mailbul, "get_session", lambda: _SahteOturum(yanit))

    assert mailbul.safe_get("https://ornek.test", timeout=1) is None
    assert yanit.kapatildi


def test_safe_get_sinir_icindeki_govdeyi_tutar(monkeypatch):
    yanit = _SahteYanit([b"merhaba", b" dunya"])
    monkeypatch.setattr(mailbul, "get_session", lambda: _SahteOturum(yanit))

    sonuc = mailbul.safe_get("https://ornek.test", timeout=1)

    assert sonuc is yanit
    assert sonuc._content == b"merhaba dunya"
    assert yanit.kapatildi
