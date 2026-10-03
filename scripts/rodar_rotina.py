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
            urllib.request.urlopen(HC + ("/fail" if falhou else ""), timeout=15).read()
        except Exception:
            pass


def main():
    if not ROT:
        print(f"ERRO: rotina '{NOME}' não está em rotinas.json")
        return 2
    run = (f"{os.environ.get('GITHUB_SERVER_URL', '')}/{os.environ.get('GITHUB_REPOSITORY', '')}"
           f"/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}")
    with tempfile.TemporaryDirectory() as tmp:
        log = pathlib.Path(tmp) / "saida.log"
        k = CHAVES.get(ROT["repo"])
        if not k:
            print("FALHOU: deploy key ausente em DEPLOY_KEYS")
            avisar(f"Rotina {NOME}: deploy key de {ROT['repo']} ausente em DEPLOY_KEYS.\nRun: {run}")
            ping(True)
            return 1
        kp = pathlib.Path(tmp) / "k"
        kp.write_text(k.strip() + "\n", encoding="utf-8")
        kp.chmod(0o600)
        env = dict(os.environ)
        env["GIT_SSH_COMMAND"] = (f"ssh -i {kp} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "
                                  f"-o UserKnownHostsFile={tmp}/kh")
        d = pathlib.Path(tmp) / "r"
        url = f"git@github.com:zarkatus/{ROT['repo']}.git"
        with open(log, "a", encoding="utf-8") as f:
            rc = subprocess.run(["git", "clone", "-q", "--depth", "1", "--branch", ROT.get("ramo", "main"), url,
                                 str(d)], env=env, stdout=f, stderr=subprocess.STDOUT, timeout=300).returncode
        if rc:
            print(f"FALHOU: clone rc={rc}")
            avisar(f"Rotina {NOME}: clone de {ROT['repo']} falhou (rc={rc}).\nRun: {run}\n\n" + cauda(log))
            ping(True)
            return 1
        env.pop("GIT_SSH_COMMAND", None)
        env["GITHUB_WORKSPACE"] = str(d)
        for i, cmd in enumerate(ROT["comandos"], 1):
            with open(log, "a", encoding="utf-8") as f:
                f.write(f"\n$ comando {i}\n")
                f.flush()
                rc = subprocess.run(cmd, shell=True, cwd=d, env=env, stdout=f, stderr=subprocess.STDOUT,
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
