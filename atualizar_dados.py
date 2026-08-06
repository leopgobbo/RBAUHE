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


def resource_csv_url(slug):
    """Descobre o recurso CSV de um conjunto pelo slug, sem depender de id fixo."""
    d = json.loads(http(f"{BASE}/api/3/action/package_show?id={slug}"))
    if not d.get("success"):
        raise RuntimeError("package_show falhou")
    for r in d["result"]["resources"]:
        if (r.get("format") or "").upper() == "CSV":
            return r["id"], r.get("url")
    raise RuntimeError("nenhum recurso CSV no conjunto")


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


def baixar_fsb():
    """Devolve dict de chaves -> {classe, cri, dpa}. Silencioso se não der."""
    try:
        rid, _ = resource_csv_url(FSB_DATASET)
    except Exception as e:
        log(f"FSB: não localizei o recurso ({e}) — seguindo sem enriquecimento")
        return {}
    try:
        try:
            rows = ckan_sql(rid)
        except Exception:
            rows = ckan_dump(rid)
    except Exception as e:
        log(f"FSB: download falhou ({e}) — seguindo sem enriquecimento")
        return {}
    if not rows:
        return {}

    cols = list(rows[0].keys())

    def acha(*termos):
        for c in cols:
            cl = c.lower()
            if all(t in cl for t in termos):
                return c
        return None

    c_ceg = acha("ceg")
    c_cls = acha("classe") or acha("class")
    c_cri = acha("categoria", "risco") or acha("cri")
    c_dpa = acha("dano") or acha("dpa")
    c_nom = acha("nom", "empreend") or acha("nom", "usina") or acha("nome")
    c_uf = acha("uf")
    if not any([c_cls, c_cri, c_dpa]):
        log(f"FSB: colunas de classificação não reconhecidas em {cols[:12]} — seguindo sem enriquecimento")
        return {}

    idx = {}
    for r in rows:
        info = {}
        if c_cls and (r.get(c_cls) or "").strip():
            info["classe"] = r[c_cls].strip()
        if c_cri and (r.get(c_cri) or "").strip():
            info["cri"] = r[c_cri].strip()
        if c_dpa and (r.get(c_dpa) or "").strip():
            info["dpa"] = r[c_dpa].strip()
        if not info:
            continue
        chaves = []
        if c_ceg and r.get(c_ceg):
            chaves += ["CEG:" + _norm(r[c_ceg]), "NUC:" + _nucleo(r[c_ceg])]
        if c_nom and r.get(c_nom):
            uf = (r.get(c_uf) or "").strip().upper() if c_uf else ""
            chaves.append("NOM:" + _norm(r[c_nom]) + "|" + uf)
        for k in chaves:
            if k and not k.endswith(":") and k not in idx:
                idx[k] = info
    log(f"FSB: {len(rows)} linhas lidas, {len(idx)} chaves de junção "
        f"(colunas usadas: ceg={c_ceg} classe={c_cls} cri={c_cri} dpa={c_dpa})")
    return idx


# ---------------------------------------------------------------- main
def main():
    usinas, origem = baixar_siga()
    log(f"SIGA: {len(usinas)} usinas hidrelétricas · {origem}")

    fsb = baixar_fsb()
    casadas = 0
    for u in usinas:
        if not fsb:
            break
        ceg = u.get("CodCEG", "")
        uf = u.get("SigUFPrincipal", "").strip().upper()
        for k in ("CEG:" + _norm(ceg), "NUC:" + _nucleo(ceg),
                  "NOM:" + _norm(u.get("NomEmpreendimento", "")) + "|" + uf):
            if k in fsb:
                u["_barragem"] = fsb[k]
                casadas += 1
                break
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
        "usinas": usinas,
    }
    dest = pathlib.Path(__file__).resolve().parent.parent / "data" / "usinas.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(saida, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"OK — {len(usinas)} usinas em {dest} ({dest.stat().st_size/1e6:.1f} MB)")
    for t in sorted(por_tipo):
        print(f"    {t}: {por_tipo[t]}")


if __name__ == "__main__":
    main()
