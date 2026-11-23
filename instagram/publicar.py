#!/usr/bin/env python3
"""Publicador do Instagram da soaone Academy.

Lê calendario.json, publica pela API oficial do Instagram (Instagram API with Instagram Login)
os posts cuja hora chegou e guarda o resultado em estado.json. Só usa a biblioteca padrão.

Comandos:
  python3 publicar.py validar             confere calendário, arquivos e URLs públicas
  python3 publicar.py listar              mostra o calendário com o status de cada post
  python3 publicar.py rodar [--simular]   publica o que estiver vencido (uma passada)
  python3 publicar.py loop [--intervalo 300]   roda para sempre (modo do container)
  python3 publicar.py publicar <id> [--forcar]  publica um post agora
  python3 publicar.py conta               mostra a conta ligada ao token e a cota de publicação
  python3 publicar.py renovar-token       renova o token de longa duração (60 dias)
"""
from __future__ import annotations

import argparse, datetime as dt, json, os, pathlib, sys, time, traceback
import urllib.error, urllib.parse, urllib.request

AQUI = pathlib.Path(__file__).resolve().parent
TZ = dt.timezone(dt.timedelta(hours=-3))  # America/Sao_Paulo (sem horário de verão desde 2019)


# ---------------------------------------------------------------- configuração
def carregar_env(arq: pathlib.Path = AQUI / ".env") -> None:
    if not arq.is_file():
        return
    for linha in arq.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        k, v = linha.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def cfg(nome: str, padrao: str | None = None) -> str:
    v = os.environ.get(nome, padrao)
    if v is None or v == "":
        raise SystemExit(f"Falta a variável {nome} no .env")
    return v


def caminho(nome_env: str, padrao: str) -> pathlib.Path:
    p = pathlib.Path(os.environ.get(nome_env, padrao))
    return p if p.is_absolute() else (AQUI / p).resolve()


# ---------------------------------------------------------------- log e aviso
def log(msg: str) -> None:
    linha = f"{dt.datetime.now(TZ):%Y-%m-%d %H:%M:%S} {msg}"
    print(linha, flush=True)
    try:
        with open(caminho("LOG_ARQUIVO", "publicar.log"), "a", encoding="utf-8") as f:
            f.write(linha + "\n")
    except OSError:
        pass


def avisar(msg: str) -> None:
    """Aviso opcional por ntfy (NOTIFICAR_URL=https://ntfy.sh/<tópico>) ou qualquer URL que aceite POST de texto."""
    url = os.environ.get("NOTIFICAR_URL")
    if not url:
        return
    try:
        req = urllib.request.Request(url, data=msg.encode(), method="POST",
                                     headers={"Title": "Instagram soaone Academy"})
        urllib.request.urlopen(req, timeout=15).read()
    except Exception as e:  # o aviso nunca derruba a publicação
        log(f"aviso não enviado: {e}")


# ---------------------------------------------------------------- HTTP
class ErroAPI(Exception):
    def __init__(self, msg: str, transitorio: bool = False):
        super().__init__(msg)
        self.transitorio = transitorio


def http(metodo: str, url: str, params: dict | None = None, timeout: int = 60) -> dict:
    params = {k: v for k, v in (params or {}).items() if v is not None}
    dados = None
    if metodo == "GET":
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    else:
        dados = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=dados, method=metodo, headers={"User-Agent": "soaone-ig/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            corpo = r.read().decode() or "{}"
            return json.loads(corpo)
    except urllib.error.HTTPError as e:
        corpo = e.read().decode(errors="replace")
        try:
            err = json.loads(corpo).get("error", {})
            msg = f"{err.get('message', corpo)} (code {err.get('code')}, subcode {err.get('error_subcode')})"
            transit = bool(err.get("is_transient")) or e.code >= 500 or err.get("code") in (1, 2, 4, 17, 32, 613)
        except ValueError:
            msg, transit = corpo[:300], e.code >= 500
        raise ErroAPI(f"HTTP {e.code}: {msg}", transitorio=transit) from None
    except (urllib.error.URLError, TimeoutError) as e:
        raise ErroAPI(f"rede: {e}", transitorio=True) from None


# ---------------------------------------------------------------- token
class Token:
    """Token de longa duração do Instagram (60 dias), renovado sozinho a cada RENOVAR_DIAS."""

    def __init__(self):
        self.arq = caminho("TOKEN_ARQUIVO", "token.json")
        env_tok = os.environ.get("IG_TOKEN", "").strip()
        d = json.loads(self.arq.read_text()) if self.arq.is_file() else None
        # token novo colado no .env vale mais que o token.json antigo
        if d is None or (env_tok and env_tok != d.get("semente")):
            tok = cfg("IG_TOKEN").strip()
            d = {"access_token": tok, "semente": tok, "obtido_em": dt.datetime.now(TZ).isoformat(), "expira_em": None}
            self._salvar(d)
        self.d = d

    def _salvar(self, d: dict) -> None:
        self.arq.write_text(json.dumps(d, indent=2))
        try:
            os.chmod(self.arq, 0o600)
        except OSError:
            pass

    @property
    def valor(self) -> str:
        return self.d["access_token"]

    def idade_dias(self) -> float:
        return (dt.datetime.now(TZ) - dt.datetime.fromisoformat(self.d["obtido_em"])).total_seconds() / 86400

    @property
    def facebook(self) -> bool:
        """Token EAA… = login do Facebook (graph.facebook.com); IGAA… = login do Instagram (graph.instagram.com)."""
        return self.valor.startswith("EAA")

    def _trocar_facebook(self) -> dict:
        app_id, segredo = os.environ.get("FB_APP_ID"), os.environ.get("FB_APP_SECRET")
        if not (app_id and segredo):
            raise ErroAPI("token do Facebook sem FB_APP_ID/FB_APP_SECRET no .env: não dá para renovar")
        return http("GET", "https://graph.facebook.com/oauth/access_token",
                    {"grant_type": "fb_exchange_token", "client_id": app_id, "client_secret": segredo,
                     "fb_exchange_token": self.valor})

    def renovar(self, forcar: bool = False) -> bool:
        dias = float(os.environ.get("RENOVAR_DIAS", "7"))
        if self.facebook and not self.d.get("longo") and os.environ.get("FB_APP_ID"):
            forcar = True  # token do Facebook recém-colado: troca já pelo de 60 dias
        if not forcar and self.idade_dias() < dias:
            return False
        if self.facebook:
            r = self._trocar_facebook()
        else:
            r = http("GET", "https://graph.instagram.com/refresh_access_token",
                     {"grant_type": "ig_refresh_token", "access_token": self.valor})
        agora = dt.datetime.now(TZ)
        exp = r.get("expires_in")
        self.d = {"access_token": r["access_token"], "semente": self.d.get("semente"), "longo": True,
                  "obtido_em": agora.isoformat(),
                  "expira_em": (agora + dt.timedelta(seconds=int(exp))).isoformat() if exp else None}
        self._salvar(self.d)
        log(f"token renovado; expira em {(self.d['expira_em'] or 'sem data')[:10]}")
        return True


# ---------------------------------------------------------------- cliente da API
class Instagram:
    def __init__(self, token: Token):
        self.token = token
        versao = os.environ.get("IG_API_VERSAO", "v24.0").strip("/")
        host = "https://graph.facebook.com" if token.facebook else "https://graph.instagram.com"
        self.base = host + (f"/{versao}" if versao else "")
        self.user = os.environ.get("IG_USER_ID") or ("" if token.facebook else "me")
        if not self.user:
            raise SystemExit("Com token do Facebook (EAA…), IG_USER_ID é obrigatório no .env")

    def _p(self, extra: dict) -> dict:
        return {**extra, "access_token": self.token.valor}

    def conta(self) -> dict:
        if self.token.facebook:
            return http("GET", f"{self.base}/{self.user}", self._p({"fields": "id,username,name,media_count"}))
        return http("GET", f"{self.base}/me", self._p({"fields": "user_id,username,account_type,media_count"}))

    def cota(self) -> dict:
        return http("GET", f"{self.base}/{self.user}/content_publishing_limit", self._p({"fields": "quota_usage,config"}))

    def criar(self, **params) -> str:
        return http("POST", f"{self.base}/{self.user}/media", self._p(params))["id"]

    def esperar(self, cid: str, limite_s: int = 600) -> None:
        """Vídeos são processados de forma assíncrona; imagens costumam vir prontas."""
        t0 = time.time()
        while True:
            st = http("GET", f"{self.base}/{cid}", self._p({"fields": "status_code,status"}))
            sc = st.get("status_code")
            if sc in ("FINISHED", "PUBLISHED") or sc is None:
                return
            if sc in ("ERROR", "EXPIRED"):
                raise ErroAPI(f"contêiner {cid}: {sc} {st.get('status', '')}")
            if time.time() - t0 > limite_s:
                raise ErroAPI(f"contêiner {cid} ainda em {sc} após {limite_s}s", transitorio=True)
            time.sleep(int(os.environ.get("ESPERA_STATUS_S", "10")))

    def publicar_container(self, cid: str) -> dict:
        mid = http("POST", f"{self.base}/{self.user}/media_publish", self._p({"creation_id": cid}))["id"]
        try:
            info = http("GET", f"{self.base}/{mid}", self._p({"fields": "permalink"}))
        except ErroAPI:
            info = {}
        return {"media_id": mid, "permalink": info.get("permalink")}


# ---------------------------------------------------------------- calendário e estado
TIPOS = {"imagem", "carrossel", "reel", "story"}


def ler_calendario() -> list[dict]:
    arq = caminho("CALENDARIO", "calendario.json")
    d = json.loads(arq.read_text(encoding="utf-8"))
    return d["posts"] if isinstance(d, dict) else d


def ler_estado() -> dict:
    arq = caminho("ESTADO", "estado.json")
    return json.loads(arq.read_text()) if arq.is_file() else {}


def salvar_estado(e: dict) -> None:
    arq = caminho("ESTADO", "estado.json")
    tmp = arq.with_suffix(".tmp")
    tmp.write_text(json.dumps(e, indent=2, ensure_ascii=False))
    tmp.replace(arq)


def quando(p: dict) -> dt.datetime:
    t = dt.datetime.fromisoformat(p["quando"])
    return t if t.tzinfo else t.replace(tzinfo=TZ)


def legenda(p: dict) -> str:
    if "legenda_arquivo" in p:
        return (caminho("LEGENDAS", "legendas") / p["legenda_arquivo"]).read_text(encoding="utf-8").strip()
    return p.get("legenda", "").strip()


def url_midia(nome: str) -> str:
    return cfg("MIDIA_URL").rstrip("/") + "/" + urllib.parse.quote(nome)


def arq_midia(nome: str) -> pathlib.Path:
    return caminho("MIDIA_DIR", "../site/ig") / nome


def modulos_liberados() -> set[int] | None:
    url = os.environ.get("LIBERACAO_URL")
    if not url:
        return None
    try:
        return set(int(m) for m in http("GET", url, timeout=20).get("modulos_liberados", []))
    except Exception as e:
        log(f"não consegui ler os módulos liberados ({e}); posts com 'modulo' ficam esperando")
        return set()


def validar_post(p: dict) -> list[str]:
    erros = []
    pid = p.get("id", "?")
    if p.get("tipo") not in TIPOS:
        erros.append(f"{pid}: tipo inválido {p.get('tipo')!r}")
    try:
        quando(p)
    except Exception:
        erros.append(f"{pid}: 'quando' inválido {p.get('quando')!r}")
    midias = p.get("midias") or []
    if not midias:
        erros.append(f"{pid}: sem mídias")
    if p.get("tipo") == "carrossel" and not 2 <= len(midias) <= 10:
        erros.append(f"{pid}: carrossel precisa de 2 a 10 mídias (tem {len(midias)})")
    if p.get("tipo") in ("imagem", "reel", "story") and len(midias) != 1:
        erros.append(f"{pid}: {p['tipo']} leva exatamente 1 mídia")
    for m in midias:
        ext = m.lower().rsplit(".", 1)[-1]
        if ext not in ("jpg", "jpeg", "mp4", "mov"):
            erros.append(f"{pid}: {m} — a API só aceita imagem JPEG e vídeo MP4/MOV")
        if p.get("tipo") == "reel" and ext not in ("mp4", "mov"):
            erros.append(f"{pid}: reel precisa de vídeo")
        f = arq_midia(m)
        if not f.is_file():
            erros.append(f"{pid}: arquivo não encontrado {f}")
        elif ext in ("jpg", "jpeg") and f.stat().st_size > 8 * 1024 * 1024:
            erros.append(f"{pid}: {m} passa de 8 MB")
    try:
        texto = legenda(p)
        if len(texto) > 2200:
            erros.append(f"{pid}: legenda com {len(texto)} caracteres (máx. 2.200)")
        if texto.count("#") > 30:
            erros.append(f"{pid}: mais de 30 hashtags")
    except FileNotFoundError as e:
        erros.append(f"{pid}: legenda não encontrada ({e.filename})")
    return erros


# ---------------------------------------------------------------- publicação
def eh_video(nome: str) -> bool:
    return nome.lower().endswith((".mp4", ".mov"))


def publicar_post(ig: Instagram, p: dict) -> dict:
    tipo, midias, texto = p["tipo"], p["midias"], legenda(p)
    if tipo == "carrossel":
        filhos = []
        for m in midias:
            if eh_video(m):
                cid = ig.criar(media_type="VIDEO", video_url=url_midia(m), is_carousel_item="true")
                ig.esperar(cid)
            else:
                cid = ig.criar(image_url=url_midia(m), is_carousel_item="true")
            filhos.append(cid)
        cid = ig.criar(media_type="CAROUSEL", children=",".join(filhos), caption=texto)
    elif tipo == "imagem":
        cid = ig.criar(image_url=url_midia(midias[0]), caption=texto)
    elif tipo == "reel":
        cid = ig.criar(media_type="REELS", video_url=url_midia(midias[0]), caption=texto,
                       share_to_feed="true" if p.get("no_feed", True) else "false",
                       cover_url=url_midia(p["capa"]) if p.get("capa") else None)
    else:  # story
        m = midias[0]
        cid = ig.criar(media_type="STORIES", **({"video_url": url_midia(m)} if eh_video(m) else {"image_url": url_midia(m)}))
    ig.esperar(cid)
    return ig.publicar_container(cid)


def rodar(simular: bool = False, so_id: str | None = None, forcar: bool = False) -> int:
    posts = ler_calendario()
    estado = ler_estado()
    agora = dt.datetime.now(TZ)
    max_tent = int(os.environ.get("MAX_TENTATIVAS", "4"))
    atraso_max = dt.timedelta(hours=float(os.environ.get("ATRASO_MAX_HORAS", "6")))
    ig = None
    liberados = None
    publicados = 0

    for p in sorted(posts, key=quando):
        pid = p["id"]
        st = estado.get(pid, {})
        if so_id and pid != so_id:
            continue
        if st.get("status") == "publicado":
            if so_id:
                log(f"{pid}: já publicado em {st.get('em')} ({st.get('permalink')})")
            continue
        if not so_id and not p.get("ativo", True):
            continue
        alvo = dt.datetime.fromisoformat(st["adiado_para"]) if st.get("adiado_para") else quando(p)
        if not so_id and agora < alvo:
            continue
        if not so_id and st.get("status") == "erro" and st.get("tentativas", 0) >= max_tent:
            continue
        if not so_id and not forcar and agora - alvo > atraso_max and not st.get("tentativas"):
            # o servidor ficou parado: não publica um post velho fora de hora; empurra para o mesmo horário amanhã
            novo = alvo + dt.timedelta(days=((agora - alvo).days + 1))
            estado[pid] = {**st, "status": "adiado", "adiado_para": novo.isoformat(),
                           "motivo": "horário perdido (publicador estava parado)"}
            log(f"{pid}: horário {alvo:%d/%m %H:%M} perdido; adiado para {novo:%d/%m %H:%M}")
            avisar(f"{pid}: horário perdido, adiado para {novo:%d/%m %H:%M}")
            continue

        erros = validar_post(p)
        if erros:
            estado[pid] = {**st, "status": "erro", "tentativas": max_tent, "erro": "; ".join(erros)}
            log("INVÁLIDO " + "; ".join(erros))
            avisar(f"{pid} inválido: {erros[0]}")
            continue

        if "modulo" in p and not forcar:
            if liberados is None:
                liberados = modulos_liberados()
            if liberados is not None and int(p["modulo"]) not in liberados:
                semanas = int(p.get("adiar_semanas", 1))
                novo = alvo + dt.timedelta(weeks=semanas)
                estado[pid] = {**st, "status": "adiado", "adiado_para": novo.isoformat(),
                               "motivo": f"módulo {p['modulo']} ainda fechado"}
                log(f"{pid}: módulo {p['modulo']} fechado; adiado para {novo:%d/%m %H:%M}")
                avisar(f"{pid}: módulo {p['modulo']} ainda fechado. Post adiado para {novo:%d/%m %H:%M}.")
                continue

        if simular:
            log(f"[simulação] publicaria {pid} ({p['tipo']}, {len(p['midias'])} mídia(s)): "
                + ", ".join(url_midia(m) for m in p["midias"]))
            continue

        if ig is None:
            tok = Token()
            try:
                tok.renovar()
            except ErroAPI as e:
                log(f"falha ao renovar o token: {e}")
                avisar(f"Falha ao renovar o token do Instagram: {e}")
            ig = Instagram(tok)

        try:
            log(f"{pid}: publicando ({p['tipo']})")
            r = publicar_post(ig, p)
            estado[pid] = {"status": "publicado", "em": dt.datetime.now(TZ).isoformat(), **r}
            publicados += 1
            log(f"{pid}: publicado {r.get('permalink') or r['media_id']}")
            avisar(f"Publicado: {pid} {r.get('permalink') or ''}")
        except ErroAPI as e:
            tent = st.get("tentativas", 0) + 1
            final = tent >= max_tent or not e.transitorio
            estado[pid] = {**st, "status": "erro", "tentativas": tent if not final else max_tent, "erro": str(e)}
            log(f"{pid}: ERRO ({'definitivo' if final else f'tentativa {tent}/{max_tent}'}): {e}")
            if final:
                avisar(f"ERRO ao publicar {pid}: {e}")
        salvar_estado(estado)
    salvar_estado(estado)
    return publicados


# ---------------------------------------------------------------- comandos
def cmd_validar() -> int:
    posts = ler_calendario()
    erros, ids = [], set()
    for p in posts:
        if p.get("id") in ids:
            erros.append(f"id repetido: {p.get('id')}")
        ids.add(p.get("id"))
        erros += validar_post(p)
    base = os.environ.get("MIDIA_URL")
    if base and "--sem-rede" not in sys.argv:
        primeiro = next((m for p in posts for m in p.get("midias", [])), None)
        if primeiro:
            try:
                req = urllib.request.Request(url_midia(primeiro), method="HEAD")
                with urllib.request.urlopen(req, timeout=15) as r:
                    ct = r.headers.get("Content-Type", "")
                print(f"URL pública ok: {url_midia(primeiro)} ({ct})")
            except Exception as e:
                erros.append(f"a mídia não está acessível em {url_midia(primeiro)}: {e}")
    for e in erros:
        print("ERRO", e)
    print(f"{len(posts)} posts, {len(erros)} problema(s)")
    return 1 if erros else 0


def cmd_listar() -> int:
    estado = ler_estado()
    for p in sorted(ler_calendario(), key=quando):
        st = estado.get(p["id"], {})
        s = st.get("status") or ("inativo" if not p.get("ativo", True) else "agendado")
        extra = st.get("permalink") or st.get("adiado_para", "")[:16].replace("T", " ") or st.get("erro", "")[:60]
        mod = f"M{p['modulo']}" if "modulo" in p else "  "
        dia = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"][quando(p).weekday()]
        print(f"{dia} {quando(p):%d/%m %H:%M}  {mod:3} {p['tipo']:9} {p['id']:22} {s:10} {extra}")
    return 0


def main(argv=None) -> int:
    carregar_env()
    ap = argparse.ArgumentParser(description="Publicador do Instagram da soaone Academy")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("validar").add_argument("--sem-rede", action="store_true")
    sub.add_parser("listar")
    r = sub.add_parser("rodar"); r.add_argument("--simular", action="store_true")
    lp = sub.add_parser("loop"); lp.add_argument("--intervalo", type=int, default=300)
    pb = sub.add_parser("publicar"); pb.add_argument("id"); pb.add_argument("--forcar", action="store_true")
    pb.add_argument("--simular", action="store_true")
    sub.add_parser("conta")
    sub.add_parser("renovar-token")
    a = ap.parse_args(argv)

    if a.cmd == "validar":
        return cmd_validar()
    if a.cmd == "listar":
        return cmd_listar()
    if a.cmd == "rodar":
        rodar(simular=a.simular)
        return 0
    if a.cmd == "publicar":
        rodar(simular=a.simular, so_id=a.id, forcar=a.forcar)
        return 0
    if a.cmd == "conta":
        tok = Token()
        try:
            tok.renovar()
        except ErroAPI as e:
            print("aviso:", e)
        ig = Instagram(tok)
        print(f"API: {ig.base} (token {'do Facebook' if tok.facebook else 'do Instagram'})")
        print(json.dumps(ig.conta(), indent=2, ensure_ascii=False))
        try:
            print(json.dumps(ig.cota(), indent=2))
        except ErroAPI as e:
            print("cota:", e)
        return 0
    if a.cmd == "renovar-token":
        Token().renovar(forcar=True)
        return 0
    if a.cmd == "loop":
        log(f"publicador iniciado; verificando a cada {a.intervalo}s")
        while True:
            try:
                rodar()
            except Exception:
                log("falha inesperada:\n" + traceback.format_exc())
            time.sleep(a.intervalo)
    return 0


if __name__ == "__main__":
    sys.exit(main())
