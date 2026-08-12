#!/usr/bin/env python3
"""
Monta data/usinas.json a partir das bases abertas da ANEEL.

  1. SIGA  — cadastro de todas as usinas de geração (obrigatório)
  2. FSB   — fiscalização de segurança de barragens (opcional, enriquece)

Nenhum valor é estimado, completado ou corrigido. O script apenas filtra as
linhas de UHE, PCH e CGH, mantém as colunas usadas pelo site e, quando consegue
casar a chave, anexa a classificação de barragem publicada pela própria ANEEL.

Uso:  python3 scripts/atualizar_dados.py
"""
import csv, io, json, re, sys, unicodedata, datetime, pathlib
import urllib.request, urllib.parse

BASE = "https://dadosabertos.aneel.gov.br"
UA = {"User-Agent": "hidro-research/2.0 (consumo de dados abertos ANEEL)"}
TIPOS = {"UHE", "PCH", "CGH"}

SIGA_RECURSOS = [
    "2f65a1b0-19b8-4360-8238-b34ab4693d55",   # siga-empreendimentos-geracao-diario.csv
    "11ec447d-698d-4ab8-977f-b424d5deee6a",   # siga-empreendimentos-geracao.csv (mensal)
]
SIGA_COLS = ["NomEmpreendimento","CodCEG","IdeNucleoCEG","SigUFPrincipal","SigTipoGeracao","DscFaseUsina",
             "DscTipoOutorga","DatEntradaOperacao","MdaPotenciaOutorgadaKw",
             "MdaPotenciaFiscalizadaKw","MdaGarantiaFisicaKw","NumCoordNEmpreendimento",
             "NumCoordEEmpreendimento","DatInicioVigencia","DatFimVigencia",
             "DscPropriRegimePariticipacao","DscSubBacia","DscMuninicpios"]
SIGA_DATASET = "siga-sistema-de-informacoes-de-geracao-da-aneel"
FSB_DATASET = "fsb-fiscalizacao-de-seguranca-de-barragens"

log = lambda *a, **k: print(*a, file=sys.stderr, flush=True, **k)


def http(url, timeout=150):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout).read()


def ckan_sql(rid, where=None, cols=None):
    sel = ",".join('"%s"' % c for c in cols) if cols else "*"
    sql = f'SELECT {sel} FROM "{rid}"' + (f" WHERE {where}" if where else "")
    d = json.loads(http(BASE + "/api/3/action/datastore_search_sql?sql=" + urllib.parse.quote(sql)))
    if not d.get("success"):
        raise RuntimeError("datastore_search_sql recusou a consulta")
    recs = d["result"]["records"]
    if not recs:
        raise RuntimeError("consulta sem registros")
    return recs


def ckan_dump(rid):
    txt = http(f"{BASE}/datastore/dump/{rid}?bom=True").decode("utf-8-sig", errors="replace")
    return list(csv.DictReader(io.StringIO(txt)))


def recursos_csv(slug):
    """Lista todos os recursos CSV de um conjunto, sem depender de id fixo."""
    d = json.loads(http(f"{BASE}/api/3/action/package_show?id={slug}"))
    if not d.get("success"):
        raise RuntimeError("package_show falhou")
    out = [(r["id"], r.get("name") or "", r.get("url") or "")
           for r in d["result"]["resources"]
           if (r.get("format") or "").upper() == "CSV"]
    if not out:
        raise RuntimeError("nenhum recurso CSV no conjunto")
    return out


# ---------------------------------------------------------------- SIGA
def _csv_direto(url):
    txt = http(url, timeout=300).decode("utf-8-sig", errors="replace")
    return list(csv.DictReader(io.StringIO(txt)))


def baixar_siga():
    """Tenta várias rotas até conseguir a base. Registra cada tentativa na hora."""
    estrategias = []
    for rid in SIGA_RECURSOS:
        estrategias.append((f"datastore_search_sql · {rid[:8]}",
                            lambda r=rid: ckan_sql(r, "\"SigTipoGeracao\" IN ('UHE','PCH','CGH')", SIGA_COLS)))
        estrategias.append((f"datastore dump CSV · {rid[:8]}", lambda r=rid: ckan_dump(r)))
    # última rota: URL publicada do recurso, direto do package_show
    def via_package():
        d = json.loads(http(f"{BASE}/api/3/action/package_show?id={SIGA_DATASET}", timeout=120))
        if not d.get("success"):
            raise RuntimeError("package_show falhou")
        erros = []
        for r in d["result"]["resources"]:
            if (r.get("format") or "").upper() != "CSV" or not r.get("url"):
                continue
            try:
                linhas = _csv_direto(r["url"])
                if linhas:
                    return linhas
            except Exception as e:
                erros.append(f"{r.get('name','?')}: {e}")
        raise RuntimeError("nenhum CSV baixável — " + "; ".join(erros[:3]))
    estrategias.append(("URL direta do recurso (package_show)", via_package))

    tentativas = []
    for nome, fn in estrategias:
        log(f"SIGA: tentando {nome}…")
        try:
            recs = fn()
            out = [{c: (r.get(c) or "").strip() for c in SIGA_COLS} for r in recs
                   if (r.get("SigTipoGeracao") or "").strip().upper() in TIPOS]
            if not out:
                raise RuntimeError(f"{len(recs)} linhas lidas, nenhuma de UHE/PCH/CGH")
            log(f"SIGA: OK por {nome} — {len(out)} hidrelétricas")
            return out, nome
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            log(f"SIGA:   falhou — {msg[:200]}")
            tentativas.append(f"{nome} -> {msg[:200]}")
    log("SIGA falhou em todas as estratégias:")
    for t in tentativas:
        log("   " + t)
    anterior = pathlib.Path(__file__).resolve().parent.parent / "data" / "usinas.json"
    if anterior.exists():
        log("SIGA: mantendo o snapshot anterior — o site continua no ar com os dados da última coleta bem-sucedida")
    sys.exit(1)


# ---------------------------------------------------------------- FSB
def _norm(s):
    """Maiúsculas, sem acento, só letras e dígitos. SÃO JOÃO -> SAOJOAO"""
    s = unicodedata.normalize("NFD", s or "")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^A-Z0-9]", "", s.upper())


def _nucleo(ceg):
    """PCH.PH.MG.000008-6.1 -> MG000008"""
    m = re.search(r"\.([A-Z]{2})\.(\d{4,7})", (ceg or "").upper())
    return (m.group(1) + m.group(2)) if m else ""


# ordem de severidade para consolidar vários barramentos da mesma usina
_SEV = {"BAIXO": 1, "BAIXA": 1, "MEDIO": 2, "MÉDIO": 2, "MEDIA": 2, "MÉDIA": 2, "ALTO": 3, "ALTA": 3}


def _pior(a, b):
    if not a:
        return b
    if not b:
        return a
    return a if _SEV.get(a.upper(), 0) >= _SEV.get(b.upper(), 0) else b


def baixar_fsb(diag):
    """Cruza o conjunto FSB da ANEEL. Nunca levanta exceção.

    O FSB tem uma linha por barramento e por campanha de fiscalização, então
    consolidamos por usina: fica a campanha mais recente e, entre barramentos
    da mesma usina, a classificação mais severa.
    """
    try:
        recursos = recursos_csv(FSB_DATASET)
    except Exception as e:
        diag["erro"] = f"não localizei recursos CSV: {e}"
        log("FSB: " + diag["erro"])
        return {}
    diag["recursos"] = [{"id": r[0][:8], "nome": r[1]} for r in recursos]

    rows, usado = [], None
    for rid, nome, _u in recursos:
        try:
            try:
                rows = ckan_sql(rid)
            except Exception:
                rows = ckan_dump(rid)
            if rows:
                usado = {"recurso": rid[:8], "nome_recurso": nome, "n_linhas": len(rows)}
                break
        except Exception as e:
            diag.setdefault("tentativas", []).append({"recurso": rid[:8], "erro": str(e)[:120]})
    if not rows:
        diag["erro"] = "nenhum recurso CSV legível"
        log("FSB: " + diag["erro"])
        return {}

    cols = list(rows[0].keys())
    lower = {c.lower(): c for c in cols}

    def col(*candidatos, contendo=None):
        for c in candidatos:                      # nome exato tem prioridade
            if c.lower() in lower:
                return lower[c.lower()]
        if contendo:
            for c in cols:
                cl = c.lower()
                if all(t in cl for t in contendo):
                    return c
        return None

    c_nuc = col("IdeNucleoCEG", contendo=("nucleo", "ceg"))
    c_ceg = col("CodCEG", contendo=("cod", "ceg"))
    c_usi = col("NomUsina", "NomEmpreendimento", contendo=("nom", "usina"))
    c_cls = col("IdcClassificacaoBarragens", "DscClasse", contendo=("classific",))
    c_cri = col("DscCategoriaRiscoGeral", contendo=("categoria", "risco"))
    c_dpa = col("DscDanoPotencialGeral", contendo=("dano", "potencial"))
    c_fim = col("DatFimCampanha", contendo=("fim", "campanha"))

    diag["colunas_detectadas"] = {"nucleo": c_nuc, "ceg": c_ceg, "usina": c_usi,
                                  "classe": c_cls, "cri": c_cri, "dpa": c_dpa, "campanha_fim": c_fim}
    diag.update(usado)
    if not any([c_cls, c_cri, c_dpa]):
        diag["erro"] = "colunas de classificação não reconhecidas"
        log("FSB: " + diag["erro"] + f" — colunas: {cols[:20]}")
        return {}

    # consolida por usina, mantendo a campanha mais recente
    porUsina = {}
    for r in rows:
        nuc = (r.get(c_nuc) or "").strip() if c_nuc else ""
        usi = (r.get(c_usi) or "").strip() if c_usi else ""
        chave = ("NUC", _norm(nuc)) if nuc else (("USI", _norm(usi)) if usi else None)
        if not chave or not chave[1]:
            continue
        campanha = (r.get(c_fim) or "") if c_fim else ""
        atual = porUsina.get(chave)
        if atual and atual["_campanha"] > campanha:
            continue                                   # já temos campanha mais recente
        novo = atual if (atual and atual["_campanha"] == campanha) else {"_campanha": campanha}
        if c_cls and (r.get(c_cls) or "").strip():
            novo["classe"] = str(r[c_cls]).strip().upper()[:1]
        if c_cri and (r.get(c_cri) or "").strip():
            novo["cri"] = _pior(novo.get("cri"), str(r[c_cri]).strip())
        if c_dpa and (r.get(c_dpa) or "").strip():
            novo["dpa"] = _pior(novo.get("dpa"), str(r[c_dpa]).strip())
        porUsina[chave] = novo

    idx = {}
    for (tipo, val), info in porUsina.items():
        info.pop("_campanha", None)
        idx[tipo + ":" + val] = info
    diag["chaves"] = len(idx)
    diag["usinas_no_fsb"] = len(porUsina)
    log(f"FSB: recurso {usado['recurso']} · {usado['n_linhas']} linhas · "
        f"{len(porUsina)} usinas consolidadas · classe={c_cls} cri={c_cri} dpa={c_dpa} usina={c_usi} nucleo={c_nuc}")
    return idx


# ---------------------------------------------------------------- novidades
def _chave(u):
    return u.get("CodCEG") or (u.get("NomEmpreendimento", "") + "|" + u.get("SigUFPrincipal", ""))


def _mw(u, campo="MdaPotenciaFiscalizadaKw"):
    v = (u.get(campo) or "").strip().replace(".", "").replace(",", ".")
    try:
        return round(float(v) / 1000, 4)
    except Exception:
        return None


def detectar_novidades(dest, usinas):
    """Compara com o snapshot anterior e devolve o que mudou no cadastro.

    Só reporta o que a própria ANEEL publicou de diferente entre duas coletas.
    Nada aqui é inferido: é diferença literal de campo entre dois arquivos.
    """
    hist_path = dest.parent / "historico.json"
    try:
        antes = json.loads(dest.read_text(encoding="utf-8")).get("usinas", [])
    except Exception:
        antes = []
    if not antes:
        log("novidades: sem snapshot anterior — primeira coleta, nada a comparar")
        return {"desde": None, "novas": [], "fase": [], "potencia": [], "dono": [], "removidas": 0}

    ant = {_chave(u): u for u in antes}
    atu = {_chave(u): u for u in usinas}

    novas, fase, potencia, dono = [], [], [], []
    for k, u in atu.items():
        v = ant.get(k)
        if v is None:
            novas.append({"ceg": u.get("CodCEG", ""), "nome": u.get("NomEmpreendimento", ""),
                          "tipo": u.get("SigTipoGeracao", ""), "uf": u.get("SigUFPrincipal", ""),
                          "mw": _mw(u), "fase": u.get("DscFaseUsina", "")})
            continue
        if (v.get("DscFaseUsina") or "") != (u.get("DscFaseUsina") or ""):
            fase.append({"ceg": u.get("CodCEG", ""), "nome": u.get("NomEmpreendimento", ""),
                         "tipo": u.get("SigTipoGeracao", ""), "uf": u.get("SigUFPrincipal", ""),
                         "de": v.get("DscFaseUsina", ""), "para": u.get("DscFaseUsina", "")})
        a, b = _mw(v), _mw(u)
        if a is not None and b is not None and abs(a - b) > 0.001:
            potencia.append({"ceg": u.get("CodCEG", ""), "nome": u.get("NomEmpreendimento", ""),
                             "tipo": u.get("SigTipoGeracao", ""), "uf": u.get("SigUFPrincipal", ""),
                             "de": a, "para": b})
        if (v.get("DscPropriRegimePariticipacao") or "") != (u.get("DscPropriRegimePariticipacao") or ""):
            dono.append({"ceg": u.get("CodCEG", ""), "nome": u.get("NomEmpreendimento", ""),
                         "tipo": u.get("SigTipoGeracao", ""), "uf": u.get("SigUFPrincipal", ""),
                         "de": (v.get("DscPropriRegimePariticipacao") or "")[:160],
                         "para": (u.get("DscPropriRegimePariticipacao") or "")[:160]})

    removidas = len([k for k in ant if k not in atu])
    try:
        desde = json.loads(dest.read_text(encoding="utf-8")).get("gerado_em")
    except Exception:
        desde = None

    nov = {"desde": desde, "novas": novas[:400], "fase": fase[:400],
           "potencia": potencia[:400], "dono": dono[:400], "removidas": removidas}
    log(f"novidades: {len(novas)} novas · {len(fase)} mudaram de fase · "
        f"{len(potencia)} mudaram de potência · {len(dono)} mudaram de proprietário · "
        f"{removidas} saíram da base")

    # acumular um diário de bordo, para o site mostrar histórico e não só a última rodada
    try:
        diario = json.loads(hist_path.read_text(encoding="utf-8")) if hist_path.exists() else []
    except Exception:
        diario = []
    if novas or fase or potencia or dono or removidas:
        diario.insert(0, {
            "em": datetime.datetime.now(datetime.timezone.utc).strftime("%d/%m/%Y %H:%M UTC"),
            "desde": desde, "novas": len(novas), "fase": len(fase),
            "potencia": len(potencia), "dono": len(dono), "removidas": removidas})
        diario = diario[:180]
        hist_path.write_text(json.dumps(diario, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return nov


# ---------------------------------------------------------------- main
def main():
    usinas, origem = baixar_siga()
    log(f"SIGA: {len(usinas)} usinas hidrelétricas · {origem}")

    fsb_diag = {}
    try:
        fsb = baixar_fsb(fsb_diag)
    except Exception as e:
        import traceback
        fsb_diag["erro"] = f"exceção: {type(e).__name__}: {e}"
        log("FSB: " + fsb_diag["erro"])
        traceback.print_exc()
        fsb = {}
    casadas = 0
    por_chave = {"NUC": 0, "USI": 0}
    for u in usinas:
        if not fsb:
            break
        nuc = _norm(u.get("IdeNucleoCEG", ""))
        nm = _norm(u.get("NomEmpreendimento", ""))
        for k in (("NUC:" + nuc) if nuc else "", ("USI:" + nm) if nm else ""):
            if k and k in fsb:
                u["_barragem"] = fsb[k]
                casadas += 1
                por_chave[k.split(":")[0]] += 1
                break
    fsb_diag["casadas"] = casadas
    fsb_diag["por_chave"] = por_chave
    if fsb:
        log(f"FSB: {casadas} usinas enriquecidas com classificação de barragem")

    por_tipo = {}
    for u in usinas:
        t = u["SigTipoGeracao"].strip().upper()
        por_tipo[t] = por_tipo.get(t, 0) + 1

    saida = {
        "gerado_em": datetime.datetime.now(datetime.timezone.utc).strftime("%d/%m/%Y %H:%M UTC"),
        "fonte": "ANEEL — SIGA (Sistema de Informações de Geração)",
        "fonte_barragens": "ANEEL — FSB (Fiscalização de Segurança de Barragens)" if casadas else None,
        "origem_tecnica": origem,
        "url_conjunto": f"{BASE}/dataset/siga-sistema-de-informacoes-de-geracao-da-aneel",
        "total": len(usinas),
        "por_tipo": por_tipo,
        "com_barragem": casadas,
        "diagnostico_fsb": fsb_diag,
        "usinas": usinas,
    }
    raiz = pathlib.Path(__file__).resolve().parent.parent
    dest = raiz / "data" / "usinas.json"
    dest.parent.mkdir(parents=True, exist_ok=True)

    # ---- detectar novidades comparando com o snapshot anterior ----
    try:
        saida["novidades"] = detectar_novidades(dest, usinas)
    except Exception as e:
        log(f"novidades: falhou ({e}) — seguindo sem comparação")
        saida["novidades"] = {"desde": None, "novas": [], "fase": [],
                              "potencia": [], "dono": [], "removidas": 0}

    dest.write_text(json.dumps(saida, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"OK — {len(usinas)} usinas em {dest} ({dest.stat().st_size/1e6:.1f} MB)")
    for t in sorted(por_tipo):
        print(f"    {t}: {por_tipo[t]}")


def _protegido():
    import traceback
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        log("ERRO FATAL na coleta — traceback completo abaixo:")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    _protegido()
