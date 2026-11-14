#!/usr/bin/env python3
"""Checagem CANONICA de uma pasta de site estatico (sites/<label>) do tipo "multi" (ex.: rhia-innovapeople).

Fonte unica da regra: este arquivo, em repo PUBLICO controlado pelo administrador. Duas chamadas, mesma regra:
  - o publicador (scripts/multi.py) roda ANTES de publicar: e a trava de verdade (o colaborador nao altera este codigo);
  - o repo do site baixa este arquivo e roda em casa (scripts/checar.py): o mesmo veredito, antes do push.
Uso:  python checar_pasta_site.py <pasta-do-site> <label>        (sai 0 = liberado; 1 = reprovado)
Biblioteca: checar(pasta, label) -> (erros, avisos); rotulo_valido(label). So biblioteca padrao.

Politica (por que cada trava existe):
  - label: vira o nome DNS e o nome do projeto na Cloudflare; so [a-z0-9-], 1 a 30 caracteres.
  - credencial/segredo no conteudo: site estatico e publico por definicao (VAZ-01).
  - banco de dados de terceiro chamado do navegador: consumo sem provisionamento do fornecedor (CPP-01/SUP-01);
    fale com o administrador antes.
  - icones: ICO-01 (todo site nasce com aba, tela de inicio e navegador).
  - sem codigo de servidor: a pasta e publicada como arquivos estaticos; funcoes/Workers nao entram.
"""
import base64
import json
import os
import pathlib
import re
import struct
import sys

LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,28}[a-z0-9])?$")
RESERVADOS = {"www", "mail"}
TETO_ARQUIVO = 25 * 1024 * 1024        # limite do Pages por arquivo
TETO_ARQUIVOS = 5000                   # o Pages aceita 20.000; aqui sobra folga e pega pasta errada
TETO_TOTAL = 150 * 1024 * 1024
EXT_TEXTO = {".html", ".htm", ".js", ".mjs", ".css", ".json", ".txt", ".svg", ".xml", ".webmanifest", ".map"}
# LISTA DE PERMISSAO (PAR-01a): o que nao esta aqui nao vai ao ar (.csv, .xlsx, .cjs, .zip, .docx... reprovam)
EXT_BINARIAS = {".ico", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".woff", ".woff2", ".ttf", ".otf", ".eot",
                ".pdf", ".mp4", ".webm", ".mp3", ".ogg", ".wav", ".m4a"}
EXT_PERMITIDAS = EXT_TEXTO | EXT_BINARIAS
# arquivos que o Cloudflare Pages trata como CODIGO/ROTA do servidor: nunca vindos de pasta de terceiro
NOME_ESPECIAL = {"_worker.js", "_routes.json", "_redirects", "_middleware.js", "_middleware.ts"}
HEADERS_OK = {"cache-control", "x-robots-tag", "content-security-policy", "permissions-policy"}
OBRIGATORIOS = ["index.html", "favicon.ico", "favicon-32.png", "apple-touch-icon.png", "icon-192.png",
                "icon-512.png", "site.webmanifest"]
NOME_PROIBIDO = re.compile(r"(^\.env|\.env($|\.)|\.pem$|\.key$|\.p12$|\.pfx$|\.bak|~$|\.sqlite3?$|\.db$|^id_(rsa|ed25519)|\.sh$|\.ps1$|\.py$)", re.I)
SEGREDOS = [
    (re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----"), "chave privada"),
    (re.compile(r"\bsbp_[a-f0-9]{30,}"), "token de gestao de banco (sbp_)"),
    (re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}"), "chave da API da Anthropic"),
    (re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9]{32,}"), "chave de API (formato sk-)"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "chave de acesso da AWS"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"), "token do GitHub"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}"), "token do GitHub (fine-grained)"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), "token do Slack"),
    (re.compile(r"\bcfut_[A-Za-z0-9]{30,}"), "token da Cloudflare"),
]
_SEG_JWT = re.compile(r"[A-Za-z0-9_.\-]+")   # trecho de token (base64url e pontos): varredura LINEAR, sem backtracking


def _jwt_service_role(t):
    """True se houver JWT cujo corpo traga service_role. Custo linear no tamanho do texto (a regex antiga era quadratica)."""
    for m in _SEG_JWT.finditer(t):
        partes = m.group(0).split(".")
        for i in range(len(partes) - 2):
            a, b, c = partes[i], partes[i + 1], partes[i + 2]
            if a.startswith("eyJ") and b.startswith("eyJ") and len(b) >= 13 and len(c) >= 10:
                try:
                    corpo = b + "=" * (-len(b) % 4)
                    if "service_role" in base64.urlsafe_b64decode(corpo).decode("utf-8", "replace"):
                        return True
                except Exception:
                    pass
    return False
# padrao montado em partes de proposito: este arquivo DETECTA e RECUSA o uso; nao chama nada
_LITERAIS_BD = ("supa" + "base.co", "functions" + "/v1/")   # substring, nunca regex: regex sobre bundle minificado e quadratica
_MSG_BD = "chamada a banco/funcao de terceiro: consumo sem provisionamento (CPP-01). Fale com o administrador antes"
AVISO_TRAVESSAO = re.compile("[\u2014\u2013]")


CPF = re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b")
_EMAIL_ESQ = re.compile(r"[A-Za-z0-9._%+-]{1,64}$")
_EMAIL_DIR = re.compile(r"[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63}){1,4}")


def _emails_distintos(t, ate):
    """Conta e-mails ancorando em '@' (custo por e-mail, nao por byte: base64 gigante nao trava)."""
    achados, i = set(), t.find("@")
    while i != -1 and len(achados) < ate:
        esq = _EMAIL_ESQ.search(t[max(0, i - 64):i])
        dir_ = _EMAIL_DIR.match(t[i + 1:i + 260])
        if esq and dir_:
            achados.add((esq.group(0) + "@" + dir_.group(0)).lower())
        i = t.find("@", i + 1)
    return achados


_CPF_SEM_PONTO = re.compile(r"(?<!\d)\d{11}(?!\d)")


def _cpf_valido(s):
    if len(set(s)) == 1:
        return False
    for n in (9, 10):
        soma = sum(int(s[i]) * (n + 1 - i) for i in range(n))
        if (soma * 10 % 11) % 10 != int(s[n]):
            return False
    return True


def dados_pessoais(t):
    """Motivo (str) se o texto parece base de dados de pessoas embutida; senao None. LGPD: site estatico e publico."""
    cpfs = {re.sub(r"\D", "", c) for c in CPF.findall(t)}
    cpfs |= {c for c in _CPF_SEM_PONTO.findall(t) if _cpf_valido(c)}
    if len(cpfs) >= 3:
        return f"{len(cpfs)} CPFs distintos"
    emails = _emails_distintos(t, 40)
    if len(emails) >= 40:
        return "40 ou mais e-mails distintos"
    return None


def rotulo_valido(label):
    return bool(LABEL.match(label)) and label not in RESERVADOS


def _png_info(p):
    """(largura, altura, tipo_de_cor) do cabecalho PNG, ou None."""
    try:
        with open(p, "rb") as f:
            h = f.read(33)
        if h[:8] != b"\x89PNG\r\n\x1a\n":
            return None
        w, a = struct.unpack(">II", h[16:24])
        return w, a, h[25]
    except OSError:
        return None


def _texto(p):
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def checar(pasta, label):
    pasta = pathlib.Path(pasta)
    erros, avisos = [], []
    if not rotulo_valido(label):
        erros.append(f"nome '{label}' invalido: use so letras minusculas, numeros e hifen (1 a 30), sem 'www'/'mail'")
    if not pasta.is_dir():
        return erros + [f"pasta {pasta} nao existe"], avisos
    arquivos = []
    for raiz, dirs, nomes in os.walk(pasta):
        r = pathlib.Path(raiz)
        rel_dir = r.relative_to(pasta)
        for d in list(dirs):   # link simbolico seria seguido pelo publicador e levaria arquivo de FORA ao ar
            if (r / d).is_symlink():
                erros.append(f"{(rel_dir / d).as_posix()}: link simbolico nao e permitido")
                dirs.remove(d)
        # dotdirs nao sao publicados (so .well-known); node_modules e sinal de pasta errada
        dirs[:] = [d for d in dirs if not (d.startswith(".") and d != ".well-known")]
        if "node_modules" in dirs:
            erros.append(f"{rel_dir / 'node_modules'}: nao publique node_modules (gere o site pronto)")
            dirs.remove("node_modules")
        if "functions" in dirs and str(rel_dir) == ".":
            erros.append("pasta 'functions': codigo de servidor nao e publicado; so arquivos estaticos")
            dirs.remove("functions")
        for n in nomes:
            if (r / n).is_symlink():
                erros.append(f"{(rel_dir / n).as_posix()}: link simbolico nao e permitido")
                continue
            if n.startswith(".") and rel_dir.parts[:1] != (".well-known",):
                continue
            arquivos.append(r / n)
    if len(arquivos) > TETO_ARQUIVOS:
        erros.append(f"{len(arquivos)} arquivos (teto {TETO_ARQUIVOS}): a pasta tem coisa que nao e site")
    total = 0
    for p in arquivos:
        rel = p.relative_to(pasta).as_posix()
        try:
            tam = p.stat().st_size
        except OSError:
            continue
        total += tam
        if tam > TETO_ARQUIVO:
            erros.append(f"{rel}: {tam // 1048576} MB (teto {TETO_ARQUIVO // 1048576} MB por arquivo); comprima ou hospede o video fora")
        if p.suffix.lower() in (".md", ".sql"):
            continue  # nao publicados (o montador os descarta); nao sao lidos
        if NOME_PROIBIDO.search(p.name):
            erros.append(f"{rel}: tipo de arquivo que nao pode ir ao ar (credencial, backup, banco ou script)")
            continue
        if p.name in NOME_ESPECIAL:
            erros.append(f"{rel}: arquivo de servidor/rota do Pages ({p.name}) nao e permitido; so arquivos estaticos")
            continue
        if rel == "_headers":
            continue   # validado a parte, abaixo
        suf = p.suffix.lower()
        if suf not in EXT_PERMITIDAS and not (suf == "" and rel.startswith(".well-known/")):
            erros.append(f"{rel}: tipo '{suf or 'sem extensao'}' nao esta na lista de permitidos (site estatico: html, css, js, imagens, fontes, pdf, midia)")
            continue
        if p.suffix.lower() in EXT_TEXTO and tam <= TETO_ARQUIVO:
            t = _texto(p)
            motivo = dados_pessoais(t)
            if motivo:
                erros.append(f"{rel}: parece base de dados de pessoas embutida ({motivo}). Dado pessoal nunca vai a site estatico publico (LGPD); avise o administrador")
            for rx, nome in SEGREDOS:
                if rx.search(t):
                    erros.append(f"{rel}: contem {nome}. Segredo nunca vai a site estatico; tire e avise o administrador")
            if _jwt_service_role(t):
                erros.append(f"{rel}: contem chave de servico (service_role)")
            tl = t.lower()
            if any(s in tl for s in _LITERAIS_BD):
                erros.append(f"{rel}: {_MSG_BD}")
            if p.suffix.lower() in (".html", ".htm") and AVISO_TRAVESSAO.search(t):
                avisos.append(f"{rel}: tem travessao; a marca InnovaPeople usa so hifen")
    hd = pasta / "_headers"
    if hd.is_file() and not hd.is_symlink():
        for n_, ln in enumerate(_texto(hd).splitlines(), 1):
            if not ln.strip() or ln.lstrip().startswith("#") or not ln[:1].isspace():
                continue   # linha de caminho (comeca na coluna 0) ou vazia
            nome_h = ln.strip().split(":", 1)[0].strip()
            if nome_h.startswith("!") or nome_h.lower() not in HEADERS_OK:
                erros.append(f"_headers linha {n_}: cabecalho '{nome_h[:40]}' nao permitido (so {', '.join(sorted(HEADERS_OK))})")
    if total > TETO_TOTAL:
        erros.append(f"pasta com {total // 1048576} MB (teto {TETO_TOTAL // 1048576} MB)")
    for o in OBRIGATORIOS:
        if not (pasta / o).is_file():
            erros.append(f"falta {o} (icone/ficha do site, ICO-01); crie o site com scripts/novo_site.py, que ja traz tudo")
    idx = pasta / "index.html"
    if idx.is_file():
        h = _texto(idx)
        if not re.search(r"<title>\s*\S", h, re.I):
            erros.append("index.html sem <title>")
        if not re.search(r'<html[^>]*\blang=', h, re.I):
            erros.append('index.html sem lang no <html> (ex.: lang="pt-BR")')
        if not re.search(r'name=["\']viewport["\']', h, re.I):
            erros.append("index.html sem meta viewport (a pagina precisa abrir bem no celular)")
        if not re.search(r'rel=["\'][^"\']*icon', h, re.I) or "apple-touch-icon" not in h:
            erros.append("index.html sem os links de icone (favicon e apple-touch-icon)")
        if not re.search(r'rel=["\']manifest["\']', h, re.I):
            erros.append('index.html sem <link rel="manifest" href="/site.webmanifest">')
    ap = _png_info(pasta / "apple-touch-icon.png")
    if ap:
        if ap[:2] != (180, 180):
            erros.append(f"apple-touch-icon.png deve ter 180x180 (tem {ap[0]}x{ap[1]})")
        if ap[2] in (4, 6):
            erros.append("apple-touch-icon.png deve ser OPACO (sem transparencia): o iPhone pinta o fundo de preto")
    elif (pasta / "apple-touch-icon.png").is_file():
        erros.append("apple-touch-icon.png nao e um PNG valido")
    for nome, lado in (("icon-192.png", 192), ("icon-512.png", 512)):
        i = _png_info(pasta / nome)
        if (pasta / nome).is_file() and (not i or i[:2] != (lado, lado)):
            erros.append(f"{nome} deve ser PNG {lado}x{lado}")
    wm = pasta / "site.webmanifest"
    if wm.is_file():
        try:
            m = json.loads(_texto(wm))
            if not m.get("short_name"):
                erros.append("site.webmanifest sem short_name")
        except ValueError:
            erros.append("site.webmanifest nao e JSON valido")
    return erros, avisos


def main(argv):
    if len(argv) != 3:
        print(__doc__)
        return 2
    erros, avisos = checar(argv[1], argv[2])
    for a in avisos:
        print("aviso:", a)
    for e in erros:
        print("REPROVADO:", e)
    if erros:
        print(f"\n{len(erros)} problema(s). Corrija e rode de novo; o publicador aplica esta mesma regra.")
        return 1
    print(f"liberado: sites/{argv[2]} passa em todas as travas" + (f" ({len(avisos)} aviso(s))" if avisos else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))