"""Groq model yedegi: kapatılan llama-3.3 404 olunca sonraki modele geçer."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import GROQ_MODEL_YEDEK, groq_chat_metin, load_config


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
