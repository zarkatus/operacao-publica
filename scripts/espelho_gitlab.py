"""Espelho GitHub -> GitLab (zarkatus -> zarkatus-mirror) rodando AQUI, sem VM (24/10/2026, fio diversificacao-vms).

Substitui o espelho da gcp-01 (/opt/espelho-gitlab, 43 repos e 4,6 GB de cache num disco de 29 GB). Mesma logica
de la, com duas diferencas:
  - compara antes de clonar: `ls-remote` (heads + tags) do GitHub e do GitLab; repo igual nos dois lados e pulado,
    entao a rodada tipica leva segundos e nada fica guardado em disco nenhum;
  - LOG MUDO de nomes: este repo e publico, entao o log mostra so contagens e o indice do repo; os nomes vao so ao
    aviso da central (ntfy), que e privado.
Segredos (nunca impressos): ESPELHO_CHAVES (JSON {repo: chave de deploy SOMENTE LEITURA em base64}; a lista de repos
e a lista das chaves; repo novo ganha chave pelo chaves_espelho_publico.py do notebook), GITLAB_MIRROR_PAT,
NTFY_TOPICO (aviso), HC_PING_ESPELHO_GITLAB (dead-man no Pulso; /fail quando algum repo falha nas 2 passadas).
"""
import base64
import datetime
import json
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

GH_ORG = "zarkatus"
GL_NS = "zarkatus-mirror"
GL_API = "https://gitlab.com/api/v4"

CHAVES = json.loads(os.environ.pop("ESPELHO_CHAVES", "") or "{}")
TOKEN = os.environ.pop("GITLAB_MIRROR_PAT", "").strip()
NTFY = os.environ.pop("NTFY_TOPICO", "")
HC = os.environ.pop("HC_PING_ESPELHO_GITLAB", "")
BASIC = base64.b64encode(f"oauth2:{TOKEN}".encode()).decode()
GL_ENV = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.extraHeader", "GIT_CONFIG_VALUE_0": f"Authorization: Basic {BASIC}"}


def log(m):
    print(f"{datetime.datetime.now(datetime.timezone.utc):%H:%M:%SZ} {m}", flush=True)


def avisar(titulo, texto):
    if not NTFY:
        return
    req = urllib.request.Request(f"https://ntfy.sh/{NTFY}", data=texto[:3800].encode("utf-8"),
                                 headers={"Title": titulo, "Priority": "4", "Tags": "warning"})
    try:
        urllib.request.urlopen(req, timeout=20).read()
    except Exception as ex:  # noqa: BLE001
        log(f"aviso a central falhou: {type(ex).__name__}")


def ping(falhou):
    if HC:
        try:
            urllib.request.urlopen(urllib.request.Request(HC + ("/fail" if falhou else ""),
                                   headers={"User-Agent": "operacao-publica/espelho-gitlab"}), timeout=15).read()
        except Exception:  # noqa: BLE001
            pass


def limpo(texto):
    return (texto or "").replace(TOKEN, "***").replace(BASIC, "***") if TOKEN else (texto or "")


def gitlab(method, path, data=None):
    req = urllib.request.Request(GL_API + path, method=method, data=json.dumps(data).encode() if data is not None else None)
    req.add_header("PRIVATE-TOKEN", TOKEN)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    st, corpo = 0, ""
    for tentativa in range(3):  # timeout, rede, 429 e 5xx repetem (cicatriz da VM em 24/10)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read().decode()
                return r.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            st, corpo = e.code, e.read().decode()[:300]
            if st != 429 and st < 500:
                return st, corpo
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            st, corpo = 0, str(e)
        if tentativa < 2:
            time.sleep(15 * (tentativa + 1))
    return st, corpo


_NS = {}


def projeto(nome):
    enc = urllib.parse.quote(f"{GL_NS}/{nome}", safe="")
    st, d = gitlab("GET", f"/projects/{enc}")
    if st == 200:
        return d
    if st != 404:
        raise RuntimeError(f"GET projeto -> {st}")
    if "id" not in _NS:
        s2, n = gitlab("GET", f"/namespaces/{urllib.parse.quote(GL_NS, safe='')}")
        _NS["id"] = n["id"] if s2 == 200 else None
    st, d = gitlab("POST", "/projects", {
        "name": nome, "path": nome, "namespace_id": _NS["id"], "visibility": "private",
        "initialize_with_readme": False, "shared_runners_enabled": False, "auto_devops_enabled": False,
        "builds_access_level": "disabled", "issues_access_level": "disabled",
        "merge_requests_access_level": "disabled", "wiki_access_level": "disabled"})
    if st not in (200, 201):
        raise RuntimeError(f"criar projeto -> {st}")
    return d


def permitir_force_push(proj):
    # mantem a protecao do ramo (variavel protegida de CI do GitLab depende dela) e so libera o push forcado
    st, br = gitlab("GET", f"/projects/{proj['id']}/protected_branches")
    if st == 200 and isinstance(br, list):
        for b in br:
            if not b.get("allow_force_push"):
                nome = urllib.parse.quote(b["name"], safe="")
                if gitlab("PATCH", f"/projects/{proj['id']}/protected_branches/{nome}", {"allow_force_push": True})[0] != 200:
                    gitlab("DELETE", f"/projects/{proj['id']}/protected_branches/{nome}")


def git(args, cwd=None, extra_env=None, timeout=1800):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", **(extra_env or {}))
    return subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)


def refs(saida):
    return {l.split("\t")[1]: l.split("\t")[0] for l in saida.splitlines() if "\t" in l}


def espelhar(nome, tmp):
    chave = os.path.join(tmp, "chave")
    with open(os.open(chave, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as f:
        f.write(base64.b64decode(CHAVES[nome]))
    gh_env = {"GIT_SSH_COMMAND": f"ssh -i {chave} -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=accept-new"}
    gh_url, gl_url = f"git@github.com:{GH_ORG}/{nome}.git", f"https://gitlab.com/{GL_NS}/{nome}.git"
    try:
        for tentativa in range(4):  # o GitHub recusa algumas conexoes seguidas e aceita a mesma chave minutos depois
            a = git(["ls-remote", "--heads", "--tags", "--refs", gh_url], extra_env=gh_env, timeout=120)
            if a.returncode == 0:
                break
            time.sleep(20 * (tentativa + 1))
        else:
            raise RuntimeError(f"GitHub recusou 4 vezes (chave de deploy removida?): {a.stderr.strip()[-200:]}")
        b = git(["ls-remote", "--heads", "--tags", "--refs", gl_url], extra_env=GL_ENV, timeout=120)
        if b.returncode == 0 and refs(a.stdout) == refs(b.stdout):
            return "em dia", len(refs(a.stdout))
        proj = projeto(nome)
        permitir_force_push(proj)
        espelho = os.path.join(tmp, "repo.git")
        r = git(["clone", "--mirror", "--quiet", gh_url, espelho], extra_env=gh_env)
        if r.returncode != 0:
            raise RuntimeError(f"clone falhou: {r.stderr.strip()[-200:]}")
        r = git(["push", "--prune", gl_url, "+refs/heads/*:refs/heads/*", "+refs/tags/*:refs/tags/*"], cwd=espelho, extra_env=GL_ENV)
        if r.returncode != 0:
            raise RuntimeError(f"push para o GitLab falhou: {limpo(r.stderr)[-300:]}")
        return "espelhado", len(refs(a.stdout))
    finally:
        if os.path.exists(chave):
            os.remove(chave)


def main():
    if not CHAVES or not TOKEN:
        avisar("espelho GitLab (operacao-publica): sem segredo", "ESPELHO_CHAVES ou GITLAB_MIRROR_PAT vazio no repo operacao-publica. "
               "Acao: rodar chaves_espelho_publico.py no notebook e conferir o segredo do PAT.")
        log("sem segredo: ESPELHO_CHAVES ou GITLAB_MIRROR_PAT vazio")
        return 1
    st, d = gitlab("GET", "/personal_access_tokens/self")
    if st in (401, 403):
        avisar("credencial: PAT do GitLab recusado (espelho no operacao-publica)",
               f"GET /personal_access_tokens/self -> {st}; e credencial, nao queda do GitLab. Acao: rotacionar gitlab-mirror-pat.txt "
               "(PAR-01i) e atualizar o segredo GITLAB_MIRROR_PAT do operacao-publica.")
        log(f"PAT recusado ({st})")
        return 1
    if st != 200:
        avisar("espelho GitLab (operacao-publica): GitLab nao respondeu", f"status={st}. Acao: proximo ciclo tenta de novo; se repetir, status.gitlab.com.")
        log(f"GitLab nao respondeu ({st})")
        return 1
    if d.get("expires_at"):
        dias = (datetime.date.fromisoformat(d["expires_at"]) - datetime.date.today()).days
        if dias < 30:
            avisar("credencial: PAT do GitLab expira em breve", f"expira em {d['expires_at']} ({dias} dias). Acao: rotacionar (PAR-01i).")
    nomes = sorted(CHAVES)
    indice = {n: i + 1 for i, n in enumerate(nomes)}
    conta = {"em dia": 0, "espelhado": 0}
    erro, motivo = [], {}
    for lista in (nomes, None):  # 2a passada so com os que falharam: falha transitoria nao vira alerta
        if lista is None:
            if not erro:
                break
            lista, erro = erro, []
            log(f"2a passada em {len(lista)} repo(s)")
            time.sleep(90)
        for nome in lista:
            with tempfile.TemporaryDirectory() as tmp:
                try:
                    estado, n = espelhar(nome, tmp)
                    conta[estado] += 1
                    log(f"repo #{indice[nome]:02d}: {estado} ({n} refs)")
                except Exception as e:  # noqa: BLE001
                    erro.append(nome)
                    motivo[nome] = limpo(str(e))[:200]
                    log(f"repo #{indice[nome]:02d}: erro")
            time.sleep(2)
    if erro:
        avisar("espelho GitLab (operacao-publica): falha em repositorio(s)",
               f"{len(erro)}/{len(nomes)} com erro depois de 2 passadas: " + "; ".join(f"{n} ({motivo[n]})" for n in erro)
               + ". Acao: 'GitHub recusou' = conferir a chave de deploy espelho-gitlab-publico do repo; 'GET projeto'/timeout = GitLab instavel.")
    log(f"fim: {conta['em dia']} em dia, {conta['espelhado']} espelhado(s), {len(erro)} erro, {len(nomes)} total")
    return 1 if erro else 0


if __name__ == "__main__":
    rc = main()
    ping(rc != 0)
    raise SystemExit(rc)
