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
SIGA_COLS = ["NomEmpreendimento","CodCEG","SigUFPrincipal","SigTipoGeracao","DscFaseUsina",
             "DscTipoOutorga","DatEntradaOperacao","MdaPotenciaOutorgadaKw",
             "MdaPotenciaFiscalizadaKw","MdaGarantiaFisicaKw","NumCoordNEmpreendimento",
             "NumCoordEEmpreendimento","DatInicioVigencia","DatFimVigencia",
             "DscPropriRegimePariticipacao","DscSubBacia","DscMuninicpios"]
FSB_DATASET = "fsb-fiscalizacao-de-seguranca-de-barragens"

log = lambda *a: print(*a, file=sys.stderr)


def http(url, timeout=240):
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
def baixar_siga():
    tentativas = []
    for rid in SIGA_RECURSOS:
        for nome, fn in (
            ("datastore_search_sql", lambda r=rid: ckan_sql(
                r, "\"SigTipoGeracao\" IN ('UHE','PCH','CGH')", SIGA_COLS)),
            ("dump CSV", lambda r=rid: ckan_dump(r)),
        ):
            try:
                recs = fn()
                out = [{c: (r.get(c) or "").strip() for c in SIGA_COLS} for r in recs
                       if (r.get("SigTipoGeracao") or "").strip().upper() in TIPOS]
                if out:
                    return out, f"{nome} · recurso {rid[:8]}"
            except Exception as e:
                tentativas.append(f"{nome} {rid[:8]}: {e}")
    log("SIGA falhou em todas as estratégias:", *tentativas, sep="\n  ")
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


def baixar_fsb(diag):
    """Devolve dict de chaves -> {classe, cri, dpa}. Nunca levanta exceção.

    Registra em `diag` o que encontrou, para diagnóstico posterior sem
    precisar do log do Actions.
    """
    try:
        recursos = recursos_csv(FSB_DATASET)
    except Exception as e:
        diag["erro"] = f"não localizei recursos CSV: {e}"
        log(f"FSB: {diag['erro']}")
        return {}
    diag["recursos"] = [{"id": r[0][:8], "nome": r[1]} for r in recursos]

    melhor, melhor_idx = None, {}
    for rid, nome, _url in recursos:
        try:
            try:
                rows = ckan_sql(rid)
            except Exception:
                rows = ckan_dump(rid)
        except Exception as e:
            diag.setdefault("tentativas", []).append({"recurso": rid[:8], "erro": str(e)[:120]})
            continue
        if not rows:
            continue
        cols = list(rows[0].keys())

        def acha(*termos, exatos=()):
            for c in cols:                      # prioriza igualdade exata
                if c.lower() in exatos:
                    return c
            for c in cols:
                cl = c.lower()
                if all(t in cl for t in termos):
                    return c
            return None

        c_ceg = acha("ceg")
        c_cls = acha("classe", exatos=("dscclasse", "classe", "clabarragem"))
        c_cri = acha("categoria", "risco") or acha("cri", exatos=("cri", "numcri", "dsccri"))
        c_dpa = acha("dano") or acha("dpa", exatos=("dpa", "numdpa", "dscdpa"))
        c_nom = (acha("nom", "empreend") or acha("nom", "usina")
                 or acha("nome") or acha("nom", "barragem") or acha("barragem"))
        c_uf = acha("uf", exatos=("siguf", "uf", "sigufprincipal"))

        info_cols = {"recurso": rid[:8], "nome_recurso": nome,
                     "colunas": cols[:40], "n_linhas": len(rows),
                     "ceg": c_ceg, "classe": c_cls, "cri": c_cri, "dpa": c_dpa,
                     "nome": c_nom, "uf": c_uf}
        diag.setdefault("analisados", []).append(info_cols)

        if not any([c_cls, c_cri, c_dpa]):
            continue

        idx = {}
        for r in rows:
            info = {}
            for campo, col in (("classe", c_cls), ("cri", c_cri), ("dpa", c_dpa)):
                if col and (r.get(col) or "").strip():
                    info[campo] = str(r[col]).strip()
            if not info:
                continue
            chaves = []
            if c_ceg and r.get(c_ceg):
                chaves += ["CEG:" + _norm(r[c_ceg]), "NUC:" + _nucleo(r[c_ceg])]
            if c_nom and r.get(c_nom):
                uf = (r.get(c_uf) or "").strip().upper() if c_uf else ""
                nm = _norm(r[c_nom])
                if nm:
                    chaves.append("NOM:" + nm + "|" + uf)
                    chaves.append("SONOME:" + nm)          # sem UF, último recurso
            for k in chaves:
                if k and not k.endswith(":") and k not in idx:
                    idx[k] = info
        if len(idx) > len(melhor_idx):
            melhor_idx, melhor = idx, info_cols

    if melhor:
        diag["escolhido"] = melhor
        diag["chaves"] = len(melhor_idx)
        log(f"FSB: recurso {melhor['recurso']} · {melhor['n_linhas']} linhas · "
            f"{len(melhor_idx)} chaves · colunas ceg={melhor['ceg']} classe={melhor['classe']} "
            f"cri={melhor['cri']} dpa={melhor['dpa']} nome={melhor['nome']}")
    else:
        diag["erro"] = "nenhum recurso com colunas de classificação reconhecíveis"
        log("FSB: " + diag["erro"])
    return melhor_idx


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
    fsb = baixar_fsb(fsb_diag)
    casadas = 0
    por_chave = {"CEG": 0, "NUC": 0, "NOM": 0, "SONOME": 0}
    for u in usinas:
        if not fsb:
            break
        ceg = u.get("CodCEG", "")
        nm = _norm(u.get("NomEmpreendimento", ""))
        uf = u.get("SigUFPrincipal", "").strip().upper()
        for k in ("CEG:" + _norm(ceg), "NUC:" + _nucleo(ceg),
                  "NOM:" + nm + "|" + uf, "SONOME:" + nm):
            if k in fsb:
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
    novidades = detectar_novidades(dest, usinas)
    saida["novidades"] = novidades

    dest.write_text(json.dumps(saida, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"OK — {len(usinas)} usinas em {dest} ({dest.stat().st_size/1e6:.1f} MB)")
    for t in sorted(por_tipo):
        print(f"    {t}: {por_tipo[t]}")


if __name__ == "__main__":
    main()
