"""Testes do publicador com a API do Instagram simulada (rodar: python3 -m pytest -q)."""
import datetime as dt, json, os, pathlib, sys
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import publicar as P

AGORA = dt.datetime(2026, 12, 10, 19, 2, tzinfo=P.TZ)


class FakeAPI:
    def __init__(self, liberados=(0, 1, 2), falhas=0):
        self.chamadas, self.n, self.liberados, self.falhas = [], 0, list(liberados), falhas

    def __call__(self, metodo, url, params=None, timeout=60):
        params = params or {}
        self.chamadas.append((metodo, url.split("?")[0], {k: v for k, v in params.items() if k != "access_token"}))
        if "api/v2/aulas" in url:
            return {"modulos_liberados": self.liberados}
        if "refresh_access_token" in url:
            return {"access_token": "NOVO", "expires_in": 5184000}
        if url.endswith("/media") and metodo == "POST":
            if self.falhas:
                self.falhas -= 1
                raise P.ErroAPI("HTTP 500: instável", transitorio=True)
            self.n += 1
            return {"id": f"c{self.n}"}
        if url.endswith("/media_publish"):
            return {"id": "m99"}
        if metodo == "GET" and "fields" in params and params["fields"].startswith("status_code"):
            return {"status_code": "FINISHED"}
        if metodo == "GET" and params.get("fields") == "permalink":
            return {"permalink": "https://www.instagram.com/p/xyz/"}
        raise AssertionError(f"chamada inesperada {metodo} {url}")


@pytest.fixture
def amb(tmp_path, monkeypatch):
    midia = tmp_path / "ig"; midia.mkdir()
    for n in ["a_1.jpg", "a_2.jpg", "a_3.jpg", "x.jpg", "v.mp4", "ruim.png"]:
        (midia / n).write_bytes(b"\xff\xd8 fake")
    leg = tmp_path / "legendas"; leg.mkdir()
    (leg / "a.txt").write_text("Legenda #LLM\n")
    env = {"MIDIA_URL": "https://academy.soaone.com.br/ig", "MIDIA_DIR": str(midia), "LEGENDAS": str(leg),
           "CALENDARIO": str(tmp_path / "cal.json"), "ESTADO": str(tmp_path / "estado.json"),
           "TOKEN_ARQUIVO": str(tmp_path / "token.json"), "LOG_ARQUIVO": str(tmp_path / "log.txt"),
           "IG_TOKEN": "TOK", "IG_USER_ID": "123", "LIBERACAO_URL": "https://soaone.com.br/api/v2/aulas",
           "ESPERA_STATUS_S": "0"}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("NOTIFICAR_URL", raising=False)

    class FakeDT(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return AGORA
    monkeypatch.setattr(P.dt, "datetime", FakeDT)

    def cal(posts):
        (tmp_path / "cal.json").write_text(json.dumps({"posts": posts}))
    return tmp_path, cal


def estado(tmp):
    return json.loads((tmp / "estado.json").read_text())


def carrossel(**kw):
    return {"id": "c-m2", "tipo": "carrossel", "quando": "2026-12-10T19:00:00-03:00",
            "midias": ["a_1.jpg", "a_2.jpg", "a_3.jpg"], "legenda_arquivo": "a.txt", **kw}


def test_publica_carrossel_na_ordem(amb, monkeypatch):
    tmp, cal = amb
    cal([carrossel()])
    api = FakeAPI(); monkeypatch.setattr(P, "http", api)
    assert P.rodar() == 1
    criados = [c[2] for c in api.chamadas if c[1].endswith("/media")]
    assert [c.get("image_url") for c in criados[:3]] == [f"https://academy.soaone.com.br/ig/a_{i}.jpg" for i in (1, 2, 3)]
    assert all(c["is_carousel_item"] == "true" for c in criados[:3])
    assert criados[3] == {"media_type": "CAROUSEL", "children": "c1,c2,c3", "caption": "Legenda #LLM"}
    assert [c for c in api.chamadas if c[1].endswith("/media_publish")][0][2] == {"creation_id": "c4"}
    e = estado(tmp)["c-m2"]
    assert e["status"] == "publicado" and e["permalink"].endswith("/xyz/")


def test_nao_publica_duas_vezes(amb, monkeypatch):
    tmp, cal = amb
    cal([carrossel()])
    api = FakeAPI(); monkeypatch.setattr(P, "http", api)
    P.rodar(); n = len(api.chamadas)
    assert P.rodar() == 0 and len(api.chamadas) == n


def test_futuro_e_inativo_ficam_parados(amb, monkeypatch):
    tmp, cal = amb
    cal([carrossel(id="futuro", quando="2026-12-11T19:00:00-03:00"), carrossel(id="off", ativo=False)])
    api = FakeAPI(); monkeypatch.setattr(P, "http", api)
    assert P.rodar() == 0 and api.chamadas == []


def test_modulo_fechado_adia_uma_semana(amb, monkeypatch):
    tmp, cal = amb
    cal([carrossel(modulo=3)])
    api = FakeAPI(liberados=[0, 1, 2]); monkeypatch.setattr(P, "http", api)
    assert P.rodar() == 0
    e = estado(tmp)["c-m2"]
    assert e["status"] == "adiado" and e["adiado_para"].startswith("2026-12-17T19:00")
    assert not any(c[1].endswith("/media") for c in api.chamadas)


def test_modulo_liberado_publica(amb, monkeypatch):
    tmp, cal = amb
    cal([carrossel(modulo=2)])
    monkeypatch.setattr(P, "http", FakeAPI(liberados=[0, 1, 2]))
    assert P.rodar() == 1


def test_horario_perdido_vai_para_o_dia_seguinte(amb, monkeypatch):
    tmp, cal = amb
    cal([carrossel(quando="2026-12-09T19:00:00-03:00")])
    monkeypatch.setattr(P, "http", FakeAPI())
    assert P.rodar() == 0
    assert estado(tmp)["c-m2"]["adiado_para"].startswith("2026-12-11T19:00")


def test_erro_transitorio_tenta_de_novo(amb, monkeypatch):
    tmp, cal = amb
    cal([carrossel()])
    monkeypatch.setattr(P, "http", FakeAPI(falhas=1))
    assert P.rodar() == 0
    assert estado(tmp)["c-m2"] == {"status": "erro", "tentativas": 1, "erro": "HTTP 500: instável"}
    assert P.rodar() == 1


def test_validacao_barra_png_e_carrossel_curto(amb):
    p = carrossel(midias=["ruim.png"])
    erros = P.validar_post(p)
    assert any("JPEG" in e for e in erros) and any("2 a 10" in e for e in erros)


def test_reel_e_story(amb, monkeypatch):
    tmp, cal = amb
    cal([{"id": "r", "tipo": "reel", "quando": "2026-12-10T19:00:00-03:00", "midias": ["v.mp4"], "legenda": "oi"},
         {"id": "s", "tipo": "story", "quando": "2026-12-10T19:00:00-03:00", "midias": ["x.jpg"]}])
    api = FakeAPI(); monkeypatch.setattr(P, "http", api)
    assert P.rodar() == 2
    criados = [c[2] for c in api.chamadas if c[1].endswith("/media")]
    assert criados[0]["media_type"] == "REELS" and criados[0]["video_url"].endswith("/v.mp4")
    assert criados[1] == {"media_type": "STORIES", "image_url": "https://academy.soaone.com.br/ig/x.jpg"}


def test_token_renovado_depois_de_7_dias(amb, monkeypatch):
    tmp, cal = amb
    (tmp / "token.json").write_text(json.dumps({"access_token": "VELHO", "obtido_em": "2026-12-01T10:00:00-03:00"}))
    cal([carrossel()])
    api = FakeAPI(); monkeypatch.setattr(P, "http", api)
    P.rodar()
    assert json.loads((tmp / "token.json").read_text())["access_token"] == "NOVO"


def test_calendario_real_e_valido():
    """O calendário entregue aponta para arquivos que existem em site/ig."""
    os.environ["MIDIA_DIR"] = str(pathlib.Path(__file__).parent.parent / "site" / "ig")
    os.environ["LEGENDAS"] = str(pathlib.Path(__file__).parent / "legendas")
    os.environ["CALENDARIO"] = str(pathlib.Path(__file__).parent / "calendario.json")
    erros = [e for p in P.ler_calendario() for e in P.validar_post(p)]
    assert erros == []
