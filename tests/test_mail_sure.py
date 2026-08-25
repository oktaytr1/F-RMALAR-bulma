"""Yavaş site: firma tavanı dolunca kalan sayfalar atlanır."""
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mailbul


def test_sure_doldu_mu_deadline_gecmisse():
    mailbul._thread_local.deadline = time.monotonic() - 1
    try:
        assert mailbul._sure_doldu_mu() is True
    finally:
        mailbul._deadline_temizle()


def test_sure_doldu_mu_deadline_yok():
    mailbul._deadline_temizle()
    assert mailbul._sure_doldu_mu() is False


def test_http_timeout_kalan_sureye_kirpilir():
    mailbul.MAIL_TIMEOUT = 8
    mailbul._thread_local.deadline = time.monotonic() + 1.5
    try:
        t = mailbul._http_timeout(8)
        assert 0.2 <= t <= 1.5
    finally:
        mailbul._deadline_temizle()


def test_kalan_uyku_sure_dolunca_sifir():
    mailbul._thread_local.deadline = time.monotonic() - 1
    try:
        assert mailbul._kalan_uyku(2.0) == 0.0
    finally:
        mailbul._deadline_temizle()


def test_sayfa_tara_sure_dolunca_safe_get_cagirmaz(monkeypatch):
    cagrildi = []

    def _safe_get(*_a, **_k):
        cagrildi.append(1)
        return None

    monkeypatch.setattr(mailbul, "safe_get", _safe_get)
    mailbul._thread_local.deadline = time.monotonic() - 1
    try:
        linkler, ok = mailbul.sayfa_tara("https://ornek.test", set(), set())
        assert linkler == []
        assert ok is False
        assert cagrildi == []
    finally:
        mailbul._deadline_temizle()


def test_mail_bul_sure_dolunca_sayfa_taramaz(monkeypatch):
    monkeypatch.setattr(mailbul, "logger", logging.getLogger("test_mail_sure"))
    cagrildi = []

    def _sayfa_tara(*_a, **_k):
        cagrildi.append(1)
        return [], False

    monkeypatch.setattr(mailbul, "sayfa_tara", _sayfa_tara)
    mailbul._thread_local.deadline = time.monotonic() - 1
    try:
        assert mailbul.mail_bul("ornek.test", "TEST") == []
        assert cagrildi == []
    finally:
        mailbul._deadline_temizle()


def test_safe_get_deadline_dolunca_istek_atmaz(monkeypatch):
    def _patla(*_a, **_k):
        raise AssertionError("istek atılmamalı")

    class _Oturum:
        get = staticmethod(_patla)

    monkeypatch.setattr(mailbul, "get_session", lambda: _Oturum())
    mailbul._thread_local.deadline = time.monotonic() - 1
    try:
        assert mailbul.safe_get("https://ornek.test") is None
    finally:
        mailbul._deadline_temizle()


def test_safe_get_indirme_sirasinda_deadline(monkeypatch):
    class _Yavas:
        headers = {}
        kapatildi = False

        def iter_content(self, chunk_size):
            yield b"a"
            mailbul._thread_local.deadline = time.monotonic() - 1
            yield b"b"

        def close(self):
            self.kapatildi = True

    class _Oturum:
        def get(self, *_a, **_k):
            return _Yavas()

    monkeypatch.setattr(mailbul, "get_session", lambda: _Oturum())
    mailbul._thread_local.deadline = time.monotonic() + 10
    try:
        yanit = mailbul.safe_get("https://ornek.test", timeout=1)
        assert yanit is None
    finally:
        mailbul._deadline_temizle()
