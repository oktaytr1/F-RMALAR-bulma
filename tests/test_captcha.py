"""CAPTCHA tespiti: normal SERP recaptcha script'i ile gerçek doğrulama ayrımı."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sitebul import captcha_var_mi, _google_sonuc_sayfasi_mi


class _SahteDriver:
    def __init__(self, url: str, page_source: str, *, h3_sayisi: int = 0):
        self.current_url = url
        self.page_source = page_source
        self._h3 = h3_sayisi

    def execute_script(self, script: str):
        if "querySelectorAll" in script:
            return self._h3
        return None


def test_normal_serp_recaptcha_script_captcha_degil():
    """Ekran görüntüsündeki durum: sonuç var, recaptcha kelimesi HTML'de."""
    html = """
    <html><body>
    <script src="https://www.google.com/recaptcha/api.js"></script>
    <div class="g"><a href="https://kivanckimya.com.tr/"><h3>Kıvanç Kimya</h3></a></div>
    </body></html>
    """
    d = _SahteDriver(
        "https://www.google.com/search?q=KIVANC+resmi+site",
        html,
        h3_sayisi=1,
    )
    assert _google_sonuc_sayfasi_mi(d) is True
    assert captcha_var_mi(d) is False


def test_sorry_url_captcha():
    d = _SahteDriver(
        "https://www.google.com/sorry/index?continue=https://www.google.com/search",
        "<html>recaptcha</html>",
        h3_sayisi=0,
    )
    assert captcha_var_mi(d) is True


def test_unusual_traffic_metni_captcha():
    d = _SahteDriver(
        "https://www.google.com/search?q=test",
        "<html><body>Our systems have detected unusual traffic</body></html>",
        h3_sayisi=0,
    )
    assert captcha_var_mi(d) is True


def test_sonuc_yok_metin_yok_captcha_degil():
    d = _SahteDriver(
        "https://www.google.com/search?q=test",
        "<html><body>loading</body></html>",
        h3_sayisi=0,
    )
    assert captcha_var_mi(d) is False
