#!/usr/bin/env python3
"""Executor do revezamento: roda as rotinas de rotinas.json que listam este executor em "executores" (CVO 30/10/2026:
"2 fornecedores são muito pouco, vamos chegar ao menos a 8").

Uso: python3 executor/executor.py --executor <pc|oracle|gitlab|...> [--so NOME] [--seco]

Cada rodada devida (horário de rotinas.json) é pedida ao livro do banco do InnConta (ops_reivindicar): o primeiro executor
leva, os outros pulam ("já feita por X" / "rodando em X"); executor que morre perde a vez quando o prazo (lease) vence, e
falha deixa outro tentar, até 3 vezes. A rotina roda pelo mesmo scripts/rodar_rotina.py do GitHub (log mudo, falha à
central). Ambiente do executor: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY (InnConta), DEPLOY_KEYS, NTFY_TOPICO.
Sem livro (banco fora) este executor NÃO roda (fecha), para não multiplicar rodadas; o GitHub, que roda primeiro, abre.
Só biblioteca padrão.
"""
import json, os, pathlib, subprocess, sys, urllib.request

RAIZ = pathlib.Path(__file__).resolve().parent.parent
A = sys.argv[1:]
EXEC = A[A.index("--executor") + 1] if "--executor" in A else ""
SO = A[A.index("--so") + 1] if "--so" in A else ""
SECO = "--seco" in A


def rpc(nome, corpo):
    url = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/rpc/" + nome
    k = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    req = urllib.request.Request(url, data=json.dumps(corpo).encode(), method="POST", headers={
        "apikey": k, "Authorization": "Bearer " + k, "Content-Type": "application/json", "User-Agent": "revezamento/" + EXEC})
    with urllib.request.urlopen(req, timeout=60) as r:
        t = r.read().decode() or "null"
    return json.loads(t)


def main():
    if not EXEC:
        print("uso: executor.py --executor NOME [--so ROTINA] [--seco]"); return 2
    rot = json.loads((RAIZ / "rotinas.json").read_text(encoding="utf-8"))["rotinas"]
    try:
        rpc("ops_rotina_sincronizar", {"p": rot})
    except Exception as ex:
        print(f"livro inacessível ({type(ex).__name__}): não rodo nada"); return 1
    rc_geral = 0
    for nome, r in rot.items():
        if EXEC not in r.get("executores", []) or (SO and nome != SO):
            continue
        if SECO:
            print(f"{nome}: rodada devida = {rpc('ops_slot_atual', {'p_nome': nome})} (seco: não peço)"); continue
        lease = int(r.get("timeout_s", 1500) / 60) + 15
        try:
            v = rpc("ops_reivindicar", {"p_rotina": nome, "p_executor": EXEC, "p_lease_min": lease})
        except Exception as ex:
            print(f"{nome}: livro falhou ({type(ex).__name__}); não rodo"); rc_geral = 1; continue
        if not v.get("ok"):
            print(f"{nome}: pulada ({v.get('motivo')})"); continue
        print(f"{nome}: levei a rodada {v['slot']}" + (f" (retomada de {v['retomada_de']})" if v.get("retomada_de") else ""))
        env = dict(os.environ, OPS_SLOT=v["slot"], OPS_EXECUTOR=EXEC)
        try:
            p = subprocess.run([sys.executable, str(RAIZ / "scripts" / "rodar_rotina.py"), nome], env=env, capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=r.get("timeout_s", 1500) + 600)
            ok, det = p.returncode == 0, (p.stdout.strip().splitlines() or [""])[-1]
        except subprocess.TimeoutExpired:
            ok, det = False, "tempo esgotado"
        print(f"{nome}: {'ok' if ok else 'FALHOU'} · {det}")
        rpc("ops_concluir", {"p_rotina": nome, "p_slot": v["slot"], "p_executor": EXEC, "p_ok": ok, "p_detalhe": det})
        rc_geral |= 0 if ok else 1
    return rc_geral


if __name__ == "__main__":
    sys.exit(main())
