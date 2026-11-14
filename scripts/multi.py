#!/usr/bin/env python3
"""Publicador do tipo "multi": UM repositorio privado, UMA pasta por subdominio (ex.: rhia-innovapeople).

Entrada em sites.json:
  {"repo": "rhia-innovapeople", "ramo": "main", "tipo": "multi", "pasta": "sites", "raiz": "_raiz",
   "sufixo": "rhia.innovapeople.com.br", "prefixo_projeto": "rhia-", "zona_id": "<id da zona>",
   "projeto_raiz": "rhia", "segredo_dns": "CLOUDFLARE_DNS_TOKEN_INNOVAPEOPLE"}
Convencao (zero codigo por site, PGA-01):
  sites/_raiz/      -> https://<sufixo>/                  projeto Pages <projeto_raiz>
  sites/<label>/    -> https://<label>.<sufixo>/          projeto Pages <prefixo_projeto><label>
  aposentados.txt   -> um label por linha: o site sai do ar (pagina "retirada", noindex), reversivel
Por label, e de forma idempotente: checa (checar_pasta_site, trava de verdade) -> garante o projeto Pages ->
publica se o ultimo commit que tocou a pasta mudou -> garante o dominio personalizado -> garante o CNAME na zona.
Um label que reprova NAO derruba os outros. Nada aqui apaga: projeto, dominio e DNS so sao criados.
Log publico MUDO (PAR-01a): saida de git/wrangler vai a arquivo; o detalhe das falhas volta ao chamador (central tecnica).
"""
import json
import pathlib
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import checar_pasta_site as regra  # noqa: E402

API = "https://api.cloudflare.com/client/v4"
LIMITE_PROJETOS = 95      # TTO-01: 100 projetos Pages por conta (plano gratis); para a 95
REPETE_FALHA_S = 1800     # label que falhou volta a ser tentado depois de 30 min (falha passageira), sem reavisar
BASE_HEADERS = ("/*\n  X-Content-Type-Options: nosniff\n  Referrer-Policy: strict-origin-when-cross-origin\n"
                "  X-Frame-Options: SAMEORIGIN\n/_versao.txt\n  Cache-Control: no-store\n")
STUB = ('<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" '
        'content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">'
        '<title>Pagina retirada</title><style>body{font:18px/1.5 Georgia,serif;margin:0;display:grid;'
        'place-items:center;min-height:100vh;background:#FAF5EF;color:#2a2233}main{padding:2rem;text-align:center}'
        '</style></head><body><main><h1>Esta pagina foi retirada.</h1></main></body></html>\n')


class Falha(Exception):
    def __init__(self, curto, detalhe=""):
        super().__init__(curto)
        self.curto, self.detalhe = curto, detalhe


def cf(metodo, caminho, token, corpo=None, tentativas=4):
    dados = json.dumps(corpo).encode("utf-8") if corpo is not None else None
    for t in range(tentativas):
        req = urllib.request.Request(API + caminho, data=dados, method=metodo,
                                     headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            try:
                msg = json.load(e)
            except Exception:
                msg = {}
            if (e.code == 429 or e.code >= 500) and t < tentativas - 1:
                time.sleep(5 * 3 ** t)
                continue
            return e.code, msg
        except (urllib.error.URLError, TimeoutError):
            if t == tentativas - 1:
                raise
            time.sleep(5 * 3 ** t)


def _erros(msg):
    return "; ".join(str(e.get("message", e))[:120] for e in (msg.get("errors") or [])[:3]) or "sem detalhe"


def host_de(site, label):
    return site["sufixo"] if label == site.get("raiz", "_raiz") else f"{label}.{site['sufixo']}"


def projeto_de(site, label):
    if label == site.get("raiz", "_raiz"):
        return site["projeto_raiz"]
    return f"{site['prefixo_projeto']}{label}"


def contar_projetos(c):
    n = 0
    for pg in range(1, 31):
        s, r = cf("GET", f"/accounts/{c['conta']}/pages/projects?page={pg}", c["cf_token"])
        lote = (r.get("result") or []) if s == 200 else []
        if not lote:
            break
        n += len(lote)
    return n


def garantir_projeto(c, projeto):
    s, r = cf("GET", f"/accounts/{c['conta']}/pages/projects/{projeto}", c["cf_token"])
    if s == 200:
        return (r.get("result") or {}).get("subdomain") or f"{projeto}.pages.dev"
    if s != 404:
        raise Falha(f"consulta ao projeto Pages HTTP {s}", _erros(r))
    if contar_projetos(c) >= LIMITE_PROJETOS:
        raise Falha("teto de projetos Pages (TTO-01, 95 de 100) atingido; nao cria projeto novo")
    s, r = cf("POST", f"/accounts/{c['conta']}/pages/projects", c["cf_token"],
              {"name": projeto, "production_branch": "main"})
    if s not in (200, 201):
        raise Falha(f"criar projeto Pages HTTP {s}", _erros(r))
    return (r.get("result") or {}).get("subdomain") or f"{projeto}.pages.dev"


def ultimo_deploy(c, projeto):
    s, r = cf("GET", f"/accounts/{c['conta']}/pages/projects/{projeto}/deployments?env=production", c["cf_token"])
    if s == 404:
        return None
    if s != 200:
        raise Falha(f"listar publicacoes HTTP {s}", _erros(r))
    for d in r.get("result") or []:
        if (d.get("latest_stage") or {}).get("status") == "success":
            return ((d.get("deployment_trigger") or {}).get("metadata") or {}).get("commit_hash")
    return None


def garantir_dominio(c, projeto, host):
    s, r = cf("GET", f"/accounts/{c['conta']}/pages/projects/{projeto}/domains", c["cf_token"])
    if s != 200:
        raise Falha(f"listar dominios HTTP {s}", _erros(r))
    for d in r.get("result") or []:
        if d.get("name") == host:
            return d.get("status")
    s, r = cf("POST", f"/accounts/{c['conta']}/pages/projects/{projeto}/domains", c["cf_token"], {"name": host})
    if s not in (200, 201):
        raise Falha(f"anexar dominio HTTP {s}", _erros(r))
    return (r.get("result") or {}).get("status")


def garantir_dns(c, site, host, alvo):
    """alvo = subdominio pages.dev REAL do projeto (o nome pedido pode ter sido tomado por outra conta: 14/11 'rhia' virou
    'rhia-djs'; apontar para o nome esperado cai no projeto alheio e a Cloudflare barra com erro 1014)."""
    zona, alvo = site["zona_id"], alvo.lower()
    s, r = cf("GET", f"/zones/{zona}/dns_records?name={host}", c["dns_token"])
    if s != 200:
        raise Falha(f"ler DNS HTTP {s}", _erros(r))
    achados = r.get("result") or []
    for x in achados:
        if x.get("type") == "CNAME" and (x.get("content") or "").lower() == alvo:
            return "ok"
    corpo = {"type": "CNAME", "name": host, "content": alvo, "proxied": True, "ttl": 1,
             "comment": "publicar-sites (multi): criado automaticamente"}
    if achados:
        x = achados[0]
        if len(achados) == 1 and x.get("type") == "CNAME" and (x.get("content") or "").lower().endswith(".pages.dev"):
            s, r = cf("PUT", f"/zones/{zona}/dns_records/{x['id']}", c["dns_token"], corpo)  # nosso nome, alvo pages.dev errado
            if s not in (200, 201):
                raise Falha(f"corrigir DNS HTTP {s}", _erros(r))
            return "corrigido"
        raise Falha("DNS: ja existe outro registro com este nome; nao sobrescrevo",
                    f"{host}: {[(y.get('type'), (y.get('content') or '')[:40]) for y in achados]}")
    s, r = cf("POST", f"/zones/{zona}/dns_records", c["dns_token"], corpo)
    if s not in (200, 201):
        raise Falha(f"criar DNS HTTP {s}", _erros(r))
    return "criado"


def montar(origem, destino, label, commit, retirado):
    """Copia so o que pode ir ao ar (sem dotfiles, .md, .sql) + cabecalhos-base + _versao.txt."""
    destino = pathlib.Path(destino)
    if destino.exists():
        shutil.rmtree(destino)
    destino.mkdir(parents=True)
    extra = ""
    if retirado:
        (destino / "index.html").write_text(STUB, encoding="utf-8")
        extra = "/*\n  X-Robots-Tag: noindex\n"
    else:
        origem = pathlib.Path(origem)
        for p in sorted(origem.rglob("*")):
            rel = p.relative_to(origem)
            if not p.is_file() or p.suffix.lower() in (".md", ".sql") or rel.name == "_headers":
                continue
            if any(part.startswith(".") and part != ".well-known" for part in rel.parts):
                continue
            if rel.parts[0] == "functions":
                continue
            alvo = destino / rel
            alvo.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, alvo)
        h = origem / "_headers"
        if h.is_file():
            extra = h.read_text(encoding="utf-8", errors="replace")
    (destino / "_headers").write_text(BASE_HEADERS + ("\n" + extra if extra else ""), encoding="utf-8")
    (destino / "_versao.txt").write_text(f"site={label}\ncommit={commit}\n", encoding="utf-8")


def ultimo_commit(d, caminho):
    r = subprocess.run(["git", "log", "-1", "--format=%H", "--", caminho], cwd=d, capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def ler_aposentados(d):
    arq = pathlib.Path(d) / "aposentados.txt"
    if not arq.is_file():
        return []
    out = []
    for ln in arq.read_text(encoding="utf-8", errors="replace").splitlines():
        ln = ln.split("#", 1)[0].strip()
        if ln:
            out.append(ln)
    return out


def um_label(c, site, d, tmp, label, retirado, log):
    host, projeto = host_de(site, label), projeto_de(site, label)
    pasta = pathlib.Path(d) / site["pasta"] / label
    if retirado:
        commit = ultimo_commit(d, "aposentados.txt")
    else:
        erros, _ = regra.checar(pasta, label if label != site.get("raiz", "_raiz") else "raiz")
        if erros:
            raise Falha("reprovou na checagem: " + erros[0][:90], "\n".join(erros))
        commit = ultimo_commit(d, f"{site['pasta']}/{label}")
    if not commit:
        raise Falha("sem commit para a pasta (historico incompleto)")
    alvo = garantir_projeto(c, projeto)
    pub = ultimo_deploy(c, projeto)
    acao = "em dia"
    if pub != commit or c["forcar"]:
        saida = pathlib.Path(tmp) / "w" / "out"
        montar(pasta, saida, label, commit, retirado)
        rc = c["rodar"](["npx", "--yes", "wrangler@3", "pages", "deploy", "out", f"--project-name={projeto}",
                         "--branch=main", f"--commit-hash={commit}", "--commit-message=publicar-sites",
                         "--commit-dirty=true"], pathlib.Path(tmp) / "w",
                        dict(c["env_base"], CLOUDFLARE_API_TOKEN=c["cf_token"], CLOUDFLARE_ACCOUNT_ID=c["conta"]),
                        log, 900)
        if rc:
            raise Falha(f"wrangler rc={rc}", c["cauda"](log))
        acao = "publicado"
    dom = garantir_dominio(c, projeto, host)
    dns = garantir_dns(c, site, host, alvo)
    return acao, commit, f"dominio {dom}, dns {dns}"


def rodar(site, env, tmp, c):
    """-> (head, linhas, falhas_novas, pendentes). Levanta Falha so para erro do repo inteiro (clone, pacto95)."""
    d = pathlib.Path(tmp) / "r"
    log = pathlib.Path(tmp) / "saida.log"
    (pathlib.Path(tmp) / "w").mkdir(exist_ok=True)
    url = f"git@github.com:zarkatus/{site['repo']}.git"
    if c["rodar"](["git", "clone", "-q", "--filter=blob:none", "--branch", site.get("ramo", "main"), url, str(d)],
                  tmp, env, log, 600):
        raise Falha("clone falhou", c["cauda"](log))
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=d, capture_output=True, text=True).stdout.strip()
    c["pacto95"](d, env, log)
    raiz_sites = d / site["pasta"]
    labels = sorted(p.name for p in raiz_sites.iterdir() if p.is_dir()) if raiz_sites.is_dir() else []
    aposentados = ler_aposentados(d)
    todos = sorted(set(labels) | set(aposentados))
    linhas, novas, pendentes = [], [], []
    for label in todos:
        k = f"{site['repo']}/{label}"
        retirado = label in aposentados
        if label.startswith("_") and label != site.get("raiz", "_raiz"):
            linhas.append(f"{k}: pasta '{label}' ignorada (so '{site.get('raiz', '_raiz')}' pode comecar com _)")
            continue
        try:
            acao, commit, nota = um_label(c, site, d, tmp, label, retirado, log)
            c["estado"].pop(k, None)
            linhas.append(f"{k}: {acao}{' (retirado)' if retirado else ''} {commit[:7]} · {nota} · {host_de(site, label)}")
        except Exception as ex:  # um label ruim (ou timeout) nunca derruba os outros
            curto = getattr(ex, "curto", type(ex).__name__)
            detalhe = getattr(ex, "detalhe", str(ex))
            commit = ultimo_commit(d, f"{site['pasta']}/{label}") or head
            ant = c["estado"].get(k)
            mudou = not ant or ant[0] != commit or ant[1] != curto
            c["estado"][k] = [commit, curto, time.time() if mudou else ant[2]]
            if mudou:
                linhas.append(f"{k}: FALHOU, {curto} (detalhe na central tecnica)")
                novas.append(f"{k}: {curto}\n{detalhe}")
            else:
                linhas.append(f"{k}: FALHOU de novo, {curto} (central ja avisada)")
                pendentes.append(k)
    return head, linhas, novas, pendentes