#!/usr/bin/env python3
"""Teste unitário (sem rede) do aviso de unidades retidas (I19, I4 F3). Uso: python scripts/teste_retidas.py"""
import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import publicar as p  # noqa: E402

FALHAS = 0


def ok(cond, nome):
    global FALHAS
    print(("OK    " if cond else "FALHA ") + nome)
    FALHAS += 0 if cond else 1


U = lambda n, e="retida", b="b" * 40: {"unidade": n, "estado": e, "portao": "visual", "motivo": "estilo 3 -> 5",
                                       "sha": "a" * 40, "blob_reprovado": b}
msgs = []
env = lambda texto, titulo="x", tag="x": msgs.append((titulo, texto))
est = {}
G = "c" * 40

ok(p.avisar_retidas("plat", est, [U("stack-ti")], G, env) and len(msgs) == 1, "1a mensagem ao surgir uma retida")
t = msgs[0][1]
ok("stack-ti" in t and "estilo 3 -> 5" in t and "aaaaaaa" in t and "fixar.json" in t, "texto traz unidade, portao/valor, versao no ar, acao")
# E5 (26/11/2026): o aviso nomeia o ARQUIVO infrator (e o blob reprovado dele), nao so a unidade
V = dict(U("stack-ti"), arquivos_reprovados=[{"arquivo": "js/packs/stack-ti.js", "blob": "e" * 40}, {"arquivo": "css/stack.css", "blob": "f" * 40}])
m5 = []
est5 = {}
p.avisar_retidas("plat", est5, [V], G, lambda t, titulo="x", tag="x": m5.append(t))
ok(bool(m5) and "js/packs/stack-ti.js" in m5[0] and "css/stack.css" in m5[0] and "eeeeeee" in m5[0], "E5: texto traz o arquivo infrator e o blob dele")
m6 = []
p.avisar_retidas("plat", {}, [U("stack-ti")], G, lambda t, titulo="x", tag="x": m6.append(t))
ok(bool(m6) and "arquivo" not in m6[0].lower().replace("arquivos", "") or "stack-ti" in m6[0], "E5: sem arquivos_reprovados no relatorio o aviso antigo segue valendo")
ok(msgs[0][0] != "falhou" and "retidas" in msgs[0][0], "titulo proprio (parametro), nao o fixo de falha")
ok(not p.avisar_retidas("plat", est, [U("stack-ti")], G, env) and len(msgs) == 1, "2a publicacao igual nao repete")
ok(p.avisar_retidas("plat", est, [U("stack-ti"), U("contingencia", "fixada", "")], G, env) and len(msgs) == 2
   and "contingencia" in msgs[1][1] and "stack-ti" in msgs[1][1], "conjunto mudou: UMA mensagem com todas")
ok(p.avisar_retidas("plat", est, [U("stack-ti", b="d" * 40), U("contingencia", "fixada", "")], G, env) and len(msgs) == 3,
   "versao quebrada nova (blob_reprovado) da mesma unidade = nova mensagem")
ok(p.avisar_retidas("plat", est, [], G, env) and len(msgs) == 4 and "voltaram" in msgs[3][1], "volta a zero manda 1 mensagem de volta")
ok(not p.avisar_retidas("plat", est, [], G, env) and len(msgs) == 4, "zero de novo nao repete")
est2 = {}
ok(not p.avisar_retidas("outro", est2, None, G, env) and len(msgs) == 4 and "_retidas" not in est2, "site sem a opcao (lista None) nao faz nada")
ok(not p.avisar_retidas("plat0", est2, [], G, env) and len(msgs) == 4, "primeira rodada limpa nao avisa")
# leitura do artefato
with tempfile.TemporaryDirectory() as d:
    s = {"repo": "r", "saida": ".publicar", "relatorio_unidades": "abas-independencia.json"}
    ok(p.unidades_nao_atuais(d, {"repo": "r", "saida": ".publicar"}) is None, "sem a opcao: None")
    ok(p.unidades_nao_atuais(d, s) is None, "arquivo ausente: None (nao mexe no estado)")
    (pathlib.Path(d) / ".publicar").mkdir()
    (pathlib.Path(d) / ".publicar" / "abas-independencia.json").write_text(
        json.dumps({"unidades": [{"unidade": "a", "estado": "atual"}, {"unidade": "b", "estado": "retida"},
                                 {"unidade": "c", "estado": "fixada"}]}), encoding="utf-8")
    ok([u["unidade"] for u in p.unidades_nao_atuais(d, s)] == ["b", "c"], "le o artefato e acha so estado != atual")
ok(any(s.get("relatorio_unidades") == "abas-independencia.json" for s in p.SITES), "sites.json: opcao no site da plataforma")
print("TESTE_RETIDAS_OK" if not FALHAS else f"TESTE_RETIDAS_FALHOU ({FALHAS})")
sys.exit(1 if FALHAS else 0)