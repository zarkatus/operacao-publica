#!/usr/bin/env python3
"""Publicador agnóstico dos sites do grupo (repositório PÚBLICO: Actions grátis e ilimitado).

Por que existe (CVO 03/10/2026): "Tudo que tem essa frequência absurda, entenda primeiro se é necessário
e se for, coloque em repositório público com minutos ilimitados". O deploy por push de cada site privado
consumia ~550 min/7d da franquia de 2.000 min/mês; esgotada a franquia, TODO workflow privado para.

Como funciona (site novo = 1 entrada em sites.json + 1 deploy key; zero código, PGA-01):
  1. para cada site, lê o HEAD do ramo no repo privado (deploy key SÓ LEITURA daquele repo);
  2. compara com o commit da última publicação de produção no próprio Cloudflare Pages (a verdade mora lá);
  3. se mudou (ou FORCAR=true), clona raso, roda as etapas de curadoria DO PRÓPRIO REPO e publica com wrangler.
Opcionais por site: "historico": true clona com histórico e tags (sem blobs antigos; ex.: git describe na curadoria);
"caminhos": [pathspecs do git] = só republica se algum commit desde a última publicação tocar neles (commit que só
mexe em fonte interna fica "sem mudança de app" e não troca a versão de quem está usando).

Log público MUDO por desenho (PAR-01a): saída de curadoria e wrangler vai a arquivo; o log mostra só
contagens e "etapa N falhou". O detalhe da falha vai à central técnica (ntfy, AOP-01), nunca ao log.
Só biblioteca padrão.
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import urllib.request

RAIZ = pathlib.Path(__file__).resolve().parent.parent
SITES = json.loads((RAIZ / "sites.json").read_text(encoding="utf-8"))["sites"]
_NOMES = ("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID", "NTFY_TOPICO", "HC_PING_PUBLICAR_SITES")
SEG = {n: os.environ.pop(n, "") for n in _NOMES}  # sai do ambiente: etapas do repo privado nunca herdam
try:
    CHAVES = json.loads(os.environ.pop("DEPLOY_KEYS", "") or "{}")  # {"<repo>": "<chave privada só leitura>"}
except ValueError:
    CHAVES = {}
CF_TOKEN = SEG.get("CLOUDFLARE_API_TOKEN", "")
CF_CONTA = SEG.get("CLOUDFLARE_ACCOUNT_ID", "")
ALVO = (os.environ.get("SITE") or "").strip()
FORCAR = (os.environ.get("FORCAR") or "").strip() == "true"


class Falha(Exception):
    def __init__(self, curto, detalhe=""):
        super().__init__(curto)
        self.curto, self.detalhe = curto, detalhe


def env_ssh(repo, tmp):
    k = CHAVES.get(repo)
    if not k:
        raise Falha("deploy key ausente em DEPLOY_KEYS")
    p = pathlib.Path(tmp) / "k"
    p.write_text(k.strip() + "\n", encoding="utf-8")
    p.chmod(0o600)
    e = dict(os.environ)
    e["GIT_SSH_COMMAND"] = (f"ssh -i {p} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "
                            f"-o UserKnownHostsFile={tmp}/kh")
    return e


def sha_remoto(site, env):
    url = f"git@github.com:zarkatus/{site['repo']}.git"
    r = subprocess.run(["git", "ls-remote", url, f"refs/heads/{site.get('ramo', 'main')}"],
                       env=env, capture_output=True, text=True, timeout=90)
    if r.returncode or not r.stdout.strip():
        raise Falha(f"ls-remote rc={r.returncode}", r.stderr[-800:])
    return r.stdout.split()[0]


def sha_publicado(site):
    url = (f"https://api.cloudflare.com/client/v4/accounts/{CF_CONTA}/pages/projects/"
           f"{site['projeto']}/deployments?env=production")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {CF_TOKEN}"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        res = json.load(resp).get("result") or []
    for d in res:  # mais recente primeiro; ignora publicação que não terminou bem
        if (d.get("latest_stage") or {}).get("status") == "success":
            return ((d.get("deployment_trigger") or {}).get("metadata") or {}).get("commit_hash")
    return None


def rodar(cmd, cwd, env, log, timeout=900, shell=False):
    with open(log, "a", encoding="utf-8") as f:
        f.write(f"\n$ {cmd}\n")
        f.flush()
        return subprocess.run(cmd, cwd=cwd, env=env, shell=shell, stdout=f, stderr=subprocess.STDOUT,
                              timeout=timeout).returncode


def cauda(log, n=40):
    try:
        return "\n".join(pathlib.Path(log).read_text(encoding="utf-8", errors="replace").splitlines()[-n:])
    except OSError:
        return ""


def sem_mudanca_de_app(site, d, pub, got):
    """True só quando há última publicação conhecida no clone e nenhum commit desde ela toca os `caminhos`."""
    if FORCAR or not pub or not site.get("caminhos"):
        return False
    if subprocess.run(["git", "cat-file", "-e", f"{pub}^{{commit}}"], cwd=d, capture_output=True).returncode:
        return False  # publicação anterior fora do histórico (deploy manual, force-push): publica
    r = subprocess.run(["git", "diff", "--name-only", pub, got, "--", *site["caminhos"]], cwd=d,
                       capture_output=True, text=True)
    return r.returncode == 0 and not r.stdout.strip()


def publicar(site, sha, env, tmp, pub=None):
    d = pathlib.Path(tmp) / "r"
    log = pathlib.Path(tmp) / "saida.log"
    url = f"git@github.com:zarkatus/{site['repo']}.git"
    raso = ["--filter=blob:none"] if site.get("historico") else ["--depth", "1"]
    if rodar(["git", "clone", "-q", *raso, "--branch", site.get("ramo", "main"), url, str(d)],
             tmp, env, log, timeout=600):
        raise Falha("clone falhou", cauda(log))
    got = subprocess.run(["git", "rev-parse", "HEAD"], cwd=d, capture_output=True, text=True).stdout.strip()
    if sem_mudanca_de_app(site, d, pub, got):
        return got, None
    env_etapa = dict(env, GITHUB_WORKSPACE=str(d))  # script que lê a variável em vez do argumento acha o clone
    for i, etapa in enumerate(site["etapas"], 1):
        if rodar(etapa.replace("{dir}", str(d)), d, env_etapa, log, shell=True):
            raise Falha(f"etapa {i} de curadoria reprovou", cauda(log))
    saida = d / site["saida"]
    n = sum(1 for p in saida.rglob("*") if p.is_file()) if saida.is_dir() else 0
    if not n:
        raise Falha("diretório curado vazio", cauda(log))
    e = dict(env, CLOUDFLARE_API_TOKEN=CF_TOKEN, CLOUDFLARE_ACCOUNT_ID=CF_CONTA)
    rc = rodar(["npx", "--yes", "wrangler@3", "pages", "deploy", site["saida"],
                f"--project-name={site['projeto']}", f"--branch={site.get('ramo_cf', 'main')}",
                f"--commit-hash={got}", "--commit-message=operacao-publica", "--commit-dirty=true"],
               d, e, log, timeout=900)
    if rc:
        raise Falha(f"wrangler rc={rc}", cauda(log))
    return got, n


def avisar_central(texto):
    topico = SEG.get("NTFY_TOPICO")
    if not topico:
        return
    req = urllib.request.Request(f"https://ntfy.sh/{topico}", data=texto[:3800].encode("utf-8"),
                                 headers={"Title": "operacao-publica: publicar-sites falhou", "Priority": "4",
                                          "Tags": "warning"})
    try:
        urllib.request.urlopen(req, timeout=20).read()
    except Exception as ex:  # aviso nunca derruba o ciclo
        print(f"aviso à central falhou: {type(ex).__name__}")


def ping_vivo(falhou):
    url = SEG.get("HC_PING_PUBLICAR_SITES")
    if url:
        try:
            urllib.request.urlopen(url + ("/fail" if falhou else ""), timeout=15).read()
        except Exception:
            pass


def main():
    if not (CF_TOKEN and CF_CONTA):
        print("ERRO: segredos do Cloudflare ausentes")
        return 2
    alvo = [s for s in SITES if not ALVO or s["repo"] == ALVO]
    if ALVO and not alvo:
        print(f"ERRO: site '{ALVO}' não está em sites.json")
        return 2
    # Estado de falha (cache do Actions, estado/falhas.json): commit que reprovou não é retentado nem reavisado
    # a cada 5 min; volta a tentar com commit novo ou FORCAR=true. Perder o cache = no máximo um aviso repetido.
    arq_estado = RAIZ / "estado" / "falhas.json"
    try:
        estado = json.loads(arq_estado.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        estado = {}
    falhas, pendentes = [], []
    for s in alvo:
        rem = None
        with tempfile.TemporaryDirectory() as tmp:
            try:
                env = env_ssh(s["repo"], tmp)
                rem = sha_remoto(s, env)
                pub = sha_publicado(s)
                if rem == pub and not FORCAR:
                    print(f"{s['repo']}: em dia ({rem[:7]})")
                    estado.pop(s["repo"], None)
                    continue
                conferidos = estado.setdefault("_sem_app", {})
                if conferidos.get(s["repo"]) == rem and not FORCAR:
                    print(f"{s['repo']}: em dia ({rem[:7]} sem mudança de app desde {str(pub)[:7]})")
                    continue
                if rem and (estado.get(s["repo"]) or [None])[0] == rem and not FORCAR:
                    print(f"{s['repo']}: {rem[:7]} reprovou antes; aguarda commit novo (central já avisada)")
                    pendentes.append(s["repo"])
                    continue
                got, n = publicar(s, rem, env, tmp, pub)
                estado.pop(s["repo"], None)
                if n is None:
                    conferidos[s["repo"]] = got
                    print(f"{s['repo']}: em dia ({got[:7]} sem mudança de app desde {str(pub)[:7]})")
                    continue
                conferidos.pop(s["repo"], None)
                print(f"{s['repo']}: publicado {got[:7]} ({n} arquivos; antes {str(pub)[:7]})")
            except Exception as ex:
                curto = ex.curto if isinstance(ex, Falha) else type(ex).__name__
                detalhe = ex.detalhe if isinstance(ex, Falha) else str(ex)
                if estado.get(s["repo"]) == [rem, curto]:
                    print(f"{s['repo']}: FALHOU de novo, {curto} (central já avisada)")
                    pendentes.append(s["repo"])
                else:
                    print(f"{s['repo']}: FALHOU, {curto} (detalhe na central técnica)")
                    falhas.append(f"{s['repo']}: {curto}\n{detalhe}")
                estado[s["repo"]] = [rem, curto]
    arq_estado.parent.mkdir(exist_ok=True)
    arq_estado.write_text(json.dumps(estado), encoding="utf-8")
    if falhas:
        run = f"{os.environ.get('GITHUB_SERVER_URL', '')}/{os.environ.get('GITHUB_REPOSITORY', '')}/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}"
        avisar_central("Publicação de site falhou; o site segue na versão anterior.\n"
                       f"Ação: corrigir a curadoria/credencial e redisparar (gh workflow run publicar-sites.yml "
                       f"-R zarkatus/operacao-publica -f site=<repo>).\nRun: {run}\n\n" + "\n\n".join(falhas))
    ping_vivo(bool(falhas or pendentes))
    return 1 if (falhas or pendentes) else 0


if __name__ == "__main__":
    sys.exit(main())
