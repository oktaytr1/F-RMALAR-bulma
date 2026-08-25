"""Groq model yedegi: kapatılan llama-3.3 404 olunca sonraki modele geçer."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import (
    GROQ_MODEL_YEDEK,
    LLMErisilemedi,
    groq_chat_metin,
    llm_kota_sifirla,
    load_config,
    _llm_json_parse,
    _llm_json_nesne,
)


# Her test bağımsız kota kilidiyle başlasın.
def setup_function():
    llm_kota_sifirla()


class _SahteMsg:
    def __init__(self, content):
        self.content = content


class _SahteChoice:
    def __init__(self, content):
        self.message = _SahteMsg(content)


class _SahteResp:
    def __init__(self, content):
        self.choices = [_SahteChoice(content)]


class _SahteGroq:
    def __init__(self, basarisiz):
        self.basarisiz = set(basarisiz)
        self.denenen = []
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        m = kwargs["model"]
        self.denenen.append(m)
        if m in self.basarisiz:
            raise RuntimeError(
                f"Error code: 404 - The model `{m}` does not exist or you do not have access to it. model_not_found"
            )
        return _SahteResp("NONE")


def test_yedek_listesi_eski_llama_icermez():
    assert "llama-3.3-70b-versatile" not in GROQ_MODEL_YEDEK
    assert "openai/gpt-oss-120b" in GROQ_MODEL_YEDEK


def test_config_varsayilan_gpt_oss():
    cfg = load_config()
    assert cfg["llm"]["model"] == "openai/gpt-oss-120b"


def test_groq_404_sonraki_modele_gecer():
    client = _SahteGroq({"openai/gpt-oss-120b"})
    text = groq_chat_metin(
        client, "openai/gpt-oss-120b", [{"role": "user", "content": "x"}]
    )
    assert text == "NONE"
    assert client.denenen[0] == "openai/gpt-oss-120b"
    assert "openai/gpt-oss-20b" in client.denenen


class _Sahte429:
    """İlk model TPD 429; yedek model cevap verir."""

    def __init__(self, bekle_mesaji: str):
        self.bekle_mesaji = bekle_mesaji
        self.denenen = []
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        m = kwargs["model"]
        self.denenen.append(m)
        if m == "openai/gpt-oss-120b":
            raise RuntimeError(
                f"Error code: 429 - rate_limit_exceeded. Please try again in {self.bekle_mesaji}"
            )
        return _SahteResp("ok")


def test_429_dakikalik_beklemez_yedek_modele_gecer(monkeypatch):
    """Groq TPD '4m18s' derse 5 dk uyuma; hemen yedek model."""
    uyunan = []
    monkeypatch.setattr("utils.time.sleep", lambda s: uyunan.append(s))
    client = _Sahte429("4m18.767s")
    text = groq_chat_metin(
        client, "openai/gpt-oss-120b", [{"role": "user", "content": "x"}]
    )
    assert text == "ok"
    assert uyunan == []
    assert client.denenen[0] == "openai/gpt-oss-120b"
    assert "openai/gpt-oss-20b" in client.denenen


def test_429_kisa_tpm_bekler(monkeypatch):
    """Birkaç saniyelik TPM burst için kısa uyku kalır."""
    uyunan = []
    monkeypatch.setattr("utils.time.sleep", lambda s: uyunan.append(s))

    class _Once429:
        def __init__(self):
            self.n = 0
            self.chat = self
            self.completions = self

        def create(self, **kwargs):
            self.n += 1
            if self.n == 1:
                raise RuntimeError(
                    "Error code: 429 - rate_limit. Please try again in 2.0s"
                )
            return _SahteResp("ok")

    text = groq_chat_metin(
        _Once429(), "openai/gpt-oss-120b", [{"role": "user", "content": "x"}]
    )
    assert text == "ok"
    assert uyunan
    assert uyunan[0] <= 8.0


class _Hepsi429:
    def __init__(self):
        self.denenen = []
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        self.denenen.append(kwargs["model"])
        raise RuntimeError(
            "Error code: 429 - rate_limit_exceeded. Please try again in 4m18s"
        )


def test_429_tum_modeller_sonra_bu_kosuda_llm_kapanir(monkeypatch):
    """120b ve 20b aynı TPD duvarı: bekleme yok; sonraki firmada LLM'e hiç gidilmez."""
    monkeypatch.setattr("utils.time.sleep", lambda s: None)
    client = _Hepsi429()
    try:
        groq_chat_metin(client, "openai/gpt-oss-120b", [{"role": "user", "content": "x"}])
        raise AssertionError("LLMErisilemedi beklenirdi")
    except LLMErisilemedi:
        pass
    assert len(client.denenen) >= 2
    n = len(client.denenen)
    try:
        groq_chat_metin(client, "openai/gpt-oss-120b", [{"role": "user", "content": "x"}])
        raise AssertionError("LLMErisilemedi beklenirdi")
    except LLMErisilemedi:
        pass
    assert len(client.denenen) == n


def test_llm_json_parse_dizi_ve_fence():
    assert _llm_json_parse('[{"id": 1, "supheli": true}]') == [
        {"id": 1, "supheli": True}
    ]
    assert _llm_json_parse('```json\n[{"id": 2}]\n```') == [{"id": 2}]
    assert _llm_json_parse('{"sonuclar": [{"id": 3}]}') == [{"id": 3}]
    assert _llm_json_parse("önce metin [{\"id\": 4}] sonra") == [{"id": 4}]
    assert _llm_json_parse("") == []
    assert _llm_json_parse("NONE") == []


def test_llm_json_nesne_fence_ve_onek():
    assert _llm_json_nesne('{"domain": "medema.com.tr"}') == {
        "domain": "medema.com.tr"
    }
    assert _llm_json_nesne('{"domain": null}') == {"domain": None}
    assert _llm_json_nesne('```json\n{"domain": "x.com"}\n```')["domain"] == "x.com"
    assert (
        _llm_json_nesne('Açıklama:\n{"domain": "y.com.tr"}')["domain"] == "y.com.tr"
    )
    assert _llm_json_nesne('[{"domain": "z.com"}]')["domain"] == "z.com"
    assert _llm_json_nesne("NONE") is None
    assert _llm_json_nesne("") is None


class _SahteGroqCevap(_SahteGroq):
    """İlk modelden verilen metni döndürür."""

    def __init__(self, content: str):
        super().__init__(set())
        self.content = content

    def create(self, **kwargs):
        self.denenen.append(kwargs["model"])
        return _SahteResp(self.content)


_ADAYLAR = [
    (80, "https://medema.com.tr/", "medema.com.tr"),
    (45, "https://ornekrehber.com/", "ornekrehber.com"),
]


def test_llm_domain_sec_json_aday():
    import sitebul

    eski = sitebul.groq_client, sitebul.config, sitebul.logger
    try:
        llm_kota_sifirla()
        sitebul.groq_client = _SahteGroqCevap('{"domain": "medema.com.tr"}')
        sitebul.config = {
            "llm": {
                "model": "openai/gpt-oss-120b",
                "temperature": 0.3,
                "max_tokens": 512,
            }
        }
        sitebul.logger = None
        assert sitebul.llm_domain_sec("MEDEMA İNŞAAT", _ADAYLAR) == "https://medema.com.tr/"
    finally:
        sitebul.groq_client, sitebul.config, sitebul.logger = eski


def test_llm_domain_sec_json_null():
    import sitebul

    eski = sitebul.groq_client, sitebul.config, sitebul.logger
    try:
        llm_kota_sifirla()
        sitebul.groq_client = _SahteGroqCevap('{"domain": null}')
        sitebul.config = {
            "llm": {
                "model": "openai/gpt-oss-120b",
                "temperature": 0.3,
                "max_tokens": 512,
            }
        }
        sitebul.logger = None
        assert sitebul.llm_domain_sec("MEDEMA İNŞAAT", _ADAYLAR) is None
    finally:
        sitebul.groq_client, sitebul.config, sitebul.logger = eski


def test_llm_domain_sec_json_listede_yok():
    import sitebul

    eski = sitebul.groq_client, sitebul.config, sitebul.logger
    try:
        llm_kota_sifirla()
        sitebul.groq_client = _SahteGroqCevap('{"domain": "uydurma.com"}')
        sitebul.config = {
            "llm": {
                "model": "openai/gpt-oss-120b",
                "temperature": 0.3,
                "max_tokens": 512,
            }
        }
        sitebul.logger = None
        assert sitebul.llm_domain_sec("MEDEMA İNŞAAT", _ADAYLAR) is None
    finally:
        sitebul.groq_client, sitebul.config, sitebul.logger = eski


def test_llm_domain_sec_www_ve_fence():
    import sitebul

    eski = sitebul.groq_client, sitebul.config, sitebul.logger
    try:
        llm_kota_sifirla()
        sitebul.groq_client = _SahteGroqCevap(
            '```json\n{"domain": "www.medema.com.tr"}\n```'
        )
        sitebul.config = {
            "llm": {
                "model": "openai/gpt-oss-120b",
                "temperature": 0.3,
                "max_tokens": 512,
            }
        }
        sitebul.logger = None
        assert sitebul.llm_domain_sec("MEDEMA İNŞAAT", _ADAYLAR) == "https://medema.com.tr/"
    finally:
        sitebul.groq_client, sitebul.config, sitebul.logger = eski


def test_llm_domain_sec_metin_fallback_ve_kisa_tuzak():
    import sitebul

    eski = sitebul.groq_client, sitebul.config, sitebul.logger
    try:
        llm_kota_sifirla()
        sitebul.config = {
            "llm": {
                "model": "openai/gpt-oss-120b",
                "temperature": 0.3,
                "max_tokens": 512,
            }
        }
        sitebul.logger = None
        sitebul.groq_client = _SahteGroqCevap(
            "medema.com.tr çünkü başlıkta marka geçiyor"
        )
        assert sitebul.llm_domain_sec("MEDEMA İNŞAAT", _ADAYLAR) == "https://medema.com.tr/"

        sitebul.groq_client = _SahteGroqCevap("TR")
        assert sitebul.llm_domain_sec("MEDEMA İNŞAAT", _ADAYLAR) is None

        sitebul.groq_client = _SahteGroqCevap("NONE")
        assert sitebul.llm_domain_sec("MEDEMA İNŞAAT", _ADAYLAR) is None
    finally:
        sitebul.groq_client, sitebul.config, sitebul.logger = eski
