#!/usr/bin/env python3
"""Roda uma rotina agendada de um repositório PRIVADO num runner deste repo PÚBLICO (Actions grátis e ilimitado).

Uso: python3 scripts/rodar_rotina.py <nome>   (nome = chave em rotinas.json)

O código da rotina continua no repo privado: aqui ele é clonado pela deploy key só leitura (DEPLOY_KEYS) e os
comandos de rotinas.json rodam na raiz do clone. Os segredos da rotina entram no ambiente pelo workflow, por
nome; os do operador (DEPLOY_KEYS, NTFY_TOPICO, HC_*) saem do ambiente antes de rodar código do repo privado.

Log público MUDO (PAR-01a): a saída dos comandos vai a arquivo; o log mostra só "comando N ok/falhou".
Falha manda as últimas linhas à central técnica (ntfy, AOP-01). Só biblioteca padrão.
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import urllib.request

RAIZ = pathlib.Path(__file__).resolve().parent.parent
NOME = sys.argv[1] if len(sys.argv) > 1 else ""
ROT = json.loads((RAIZ / "rotinas.json").read_text(encoding="utf-8"))["rotinas"].get(NOME)
try:
    CHAVES = json.loads(os.environ.pop("DEPLOY_KEYS", "") or "{}")
except ValueError:
    CHAVES = {}
# "escrita": true = rotina que comita de volta no repo (retrato, recorte, relatório): usa a deploy key de ESCRITA
# daquele repo (DEPLOY_KEYS_ESCRITA, só nos workflows dessas rotinas) e o git dos comandos herda o ssh dela.
try:
    CHAVES_ESCRITA = json.loads(os.environ.pop("DEPLOY_KEYS_ESCRITA", "") or "{}")
except ValueError:
    CHAVES_ESCRITA = {}
NTFY = os.environ.pop("NTFY_TOPICO", "")
HC = os.environ.pop(ROT.get("hc_segredo", "HC_PING_ROTINA"), "") if ROT else ""


def avisar(texto):
    if not NTFY:
        return
    req = urllib.request.Request(f"https://ntfy.sh/{NTFY}", data=texto[:3800].encode("utf-8"),
                                 headers={"Title": f"operacao-publica: rotina {NOME} falhou", "Priority": "4",
                                          "Tags": "warning"})
    try:
        urllib.request.urlopen(req, timeout=20).read()
    except Exception as ex:
        print(f"aviso à central falhou: {type(ex).__name__}")


def ping(falhou):
    if HC:
        try:
            # 24/10: o Pulso (Cloudflare) devolve 403 ao User-Agent padrao do urllib; o Healthchecks aceita os dois.
            urllib.request.urlopen(urllib.request.Request(HC + ("/fail" if falhou else ""),
                                   headers={"User-Agent": "operacao-publica/rodar_rotina"}), timeout=15).read()
        except Exception:
            pass


def livro(nome_rpc, corpo):
    """Livro do revezamento (ops_*, banco do InnConta): rotina com "executores" em rotinas.json roda uma vez por rodada,
    em quem chegar primeiro (executor/executor.py). Erro de livro aqui ABRE: o GitHub roda mesmo assim."""
    url, k = os.environ.get("SUPABASE_URL", "").rstrip("/"), os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    req = urllib.request.Request(url + "/rest/v1/rpc/" + nome_rpc, data=json.dumps(corpo).encode(), method="POST", headers={
        "apikey": k, "Authorization": "Bearer " + k, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode() or "null")


def main():
    if not ROT:
        print(f"ERRO: rotina '{NOME}' não está em rotinas.json")
        return 2
    for k_, v_ in ROT.get("env", {}).items():  # ambiente fixo da rotina (vale em todo executor; "$X" = segredo do executor)
        os.environ.setdefault(k_, os.path.expandvars(v_))
    slot = None
    if ROT.get("executores") and not os.environ.get("OPS_SLOT") and os.environ.get("OPS_ENSAIO", "false") != "true":
        try:
            v = livro("ops_reivindicar", {"p_rotina": NOME, "p_executor": "github", "p_lease_min": int(ROT.get("timeout_s", 1500) / 60) + 15})
            if v.get("ok"):
                slot = v["slot"]
            elif v.get("slot"):  # rodada devida já feita/rodando em outro executor
                print(f"pulada: {v.get('motivo')}")
                return 0
            # sem rodada devida = disparo manual fora de horário: roda, fora do livro
        except Exception as ex:
            print(f"livro inacessível ({type(ex).__name__}): rodo mesmo assim")
    rc = executar()
    if slot:
        try:
            livro("ops_concluir", {"p_rotina": NOME, "p_slot": slot, "p_executor": "github", "p_ok": rc == 0, "p_detalhe": f"rc={rc}"})
        except Exception:
            pass
    return rc


def executar():
    run = (f"{os.environ.get('GITHUB_SERVER_URL', '')}/{os.environ.get('GITHUB_REPOSITORY', '')}"
           f"/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}")
    with tempfile.TemporaryDirectory() as tmp:
        log = pathlib.Path(tmp) / "saida.log"
        escrita = bool(ROT.get("escrita"))
        nome_seg = "DEPLOY_KEYS_ESCRITA" if escrita else "DEPLOY_KEYS"
        k = (CHAVES_ESCRITA if escrita else CHAVES).get(ROT["repo"])
        if not k:
            print(f"FALHOU: deploy key ausente em {nome_seg}")
            avisar(f"Rotina {NOME}: deploy key de {ROT['repo']} ausente em {nome_seg}.\nRun: {run}")
            ping(True)
            return 1
        kp = pathlib.Path(tmp) / "k"
        kp.write_text(k.strip() + "\n", encoding="utf-8", newline="\n")  # LF: chave com CRLF (Windows) = "error in libcrypto"
        kp.chmod(0o600)
        env = dict(os.environ)
        # as_posix: no Windows (executor do PC) o sh do git engole a barra invertida do caminho
        env["GIT_SSH_COMMAND"] = (f"ssh -i {kp.as_posix()} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "
                                  f"-o UserKnownHostsFile={pathlib.Path(tmp).as_posix()}/kh")
        d = pathlib.Path(tmp) / "r"
        url = f"git@github.com:zarkatus/{ROT['repo']}.git"
        with open(log, "a", encoding="utf-8") as f:
            rc = subprocess.run(["git", "clone", "-q", "--depth", str(ROT.get("profundidade", 1)), "--branch", ROT.get("ramo", "main"), url,
                                 str(d)], env=env, stdout=f, stderr=subprocess.STDOUT, timeout=300).returncode
        if rc:
            print(f"FALHOU: clone rc={rc}")
            avisar(f"Rotina {NOME}: clone de {ROT['repo']} falhou (rc={rc}).\nRun: {run}\n\n" + cauda(log))
            ping(True)
            return 1
        if not escrita:
            env.pop("GIT_SSH_COMMAND", None)
        env["GITHUB_WORKSPACE"] = str(d)
        env["GITHUB_OUTPUT"] = str(pathlib.Path(tmp) / "saidas")  # "nome=valor" do script, lido pelos comandos seguintes
        for i, cmd in enumerate(ROT["comandos"], 1):
            with open(log, "a", encoding="utf-8") as f:
                f.write(f"\n$ comando {i}\n")
                f.flush()
                # Windows (executor do PC): os comandos são de bash ($VAR, ||, [ ]), nunca do cmd.exe
                alvo = [env["OPS_BASH"], "-c", cmd] if env.get("OPS_BASH") else cmd
                rc = subprocess.run(alvo, shell=not env.get("OPS_BASH"), cwd=d, env=env, stdout=f, stderr=subprocess.STDOUT,
                                    timeout=ROT.get("timeout_s", 1500)).returncode
            if rc:
                print(f"comando {i}: FALHOU rc={rc} (detalhe na central técnica)")
                avisar(f"Rotina {NOME} ({ROT['repo']}): comando {i} falhou rc={rc}.\n"
                       f"Ação: ler o detalhe abaixo e corrigir no repo privado; redisparar: gh workflow run "
                       f"{ROT.get('workflow', NOME + '.yml')} -R zarkatus/operacao-publica\nRun: {run}\n\n" + cauda(log))
                ping(True)
                return 1
            print(f"comando {i}: ok")
    ping(False)
    return 0


def cauda(log, n=40):
    try:
        return "\n".join(pathlib.Path(log).read_text(encoding="utf-8", errors="replace").splitlines()[-n:])
    except OSError:
        return ""


if __name__ == "__main__":
    sys.exit(main())
