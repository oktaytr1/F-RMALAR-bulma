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
