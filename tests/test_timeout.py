"""Google renderer timeout: kısa hata, sorgu eşleşmesi, spor skor ignore."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sitebul import (
    ChromeOturumDustu,
    _google_bu_sorgu_mu,
    _kisa_selenium_hata,
    _oturum_dustu_mu,
)
from utils import ignore_edilmeli


class _SahteDriver:
    def __init__(self, url):
        self.current_url = url


def test_kisa_selenium_hata_stacktrace_atilir():
    class _Exc:
        msg = (
            "timeout: Timed out receiving message from renderer: 45.000\n"
            "Stacktrace:\n0   chromedriver + 3427972"
        )

    kisa = _kisa_selenium_hata(_Exc())
    assert "Stacktrace" not in kisa
    assert "chromedriver +" not in kisa
    assert "Timed out receiving message from renderer" in kisa


def test_google_bu_sorgu_current_url():
    sorgu = "HİTİT ISI resmi site"
    ayni = _SahteDriver(
        "https://www.google.com/search?q=" + "H%C4%B0T%C4%B0T+ISI+resmi+site"
    )
    onceki = _SahteDriver(
        "https://www.google.com/search?q=DOGANSA+resmi+site"
    )
    assert _google_bu_sorgu_mu(ayni, sorgu) is True
    assert _google_bu_sorgu_mu(onceki, sorgu) is False


def test_google_sorry_captcha_sayfasi():
    d = _SahteDriver("https://www.google.com/sorry/index?continue=https://www.google.com/search")
    assert _google_bu_sorgu_mu(d, "BARAY resmi site") is True


def test_sofascore_ignore():
    assert ignore_edilmeli("https://www.sofascore.com/tr/football", "www.sofascore.com")


def test_oturum_dustu_tab_crashed():
    class _Exc:
        msg = "tab crashed"

    assert _oturum_dustu_mu(_Exc()) is True
    assert _oturum_dustu_mu(ChromeOturumDustu("tab crashed")) is True


def test_oturum_dustu_renderer_timeout_degil():
    class _Exc:
        msg = "timeout: Timed out receiving message from renderer: 45.000"

    assert _oturum_dustu_mu(_Exc()) is False


def test_oturum_dustu_invalid_session():
    class _Exc:
        msg = "invalid session id"

    assert _oturum_dustu_mu(_Exc()) is True
