"""Calculo diario de GEX / gamma flip / muros para indices US liquidos.

Uso tipico (SPY y QQQ, que son los que imantan al ES/MES y al NQ/MNQ):

    python gex_daily.py --simbolos SPY QQQ

Escribe en out/:
  - snapshots/chain_<SIM>_<fecha>.parquet : cadena cruda del dia (historico)
  - gex_<SIM>_<fecha>.json                : niveles + perfil de GEX del dia
  - gamma_levels.csv                      : una linea por simbolo/dia -> lo lee MT5
  - gex_history.csv                       : historico acumulado de niveles

OJO con el alcance: esto solo tiene sentido en SPX/SPY/QQQ/NDX. El oro y el DAX
no tienen un mercado de opciones que imante al subyacente de esta forma.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

import pandas as pd

from chain_source import descargar_cadena, guardar_snapshot
from gex_core import niveles_gamma
from opex_calendar import estado_opex

BASE = Path(__file__).resolve().parent
OUT = BASE / "out"

# Carpeta comun de MetaQuotes: un solo fichero lo leen los tres terminales
# (Dukascopy, Capital Point y AMP) via FILE_COMMON.
MT5_COMUN = Path.home() / "AppData/Roaming/MetaQuotes/Terminal/Common/Files/gamma_options"

# Columnas de la linea que consume MT5. Orden fijo: el indicador las lee por
# posicion, no por nombre.
COLUMNAS_MT5 = [
    "fecha",
    "simbolo",
    "spot",
    "gamma_flip",
    "call_wall",
    "put_wall",
    "regimen",
    "gex_neto",
    "dist_flip_pct",
    "dias_a_opex",
    "pre_opex",
    "ventana_debilidad",
]


def procesar(simbolo: str, hoy: dt.date, max_dias: int, rate: float, outdir: Path) -> tuple[dict, dict]:
    cadena, spot = descargar_cadena(simbolo, max_dias=max_dias, hoy=hoy)
    guardar_snapshot(cadena, spot, simbolo, outdir / "snapshots", hoy)

    niveles, con_gex, perfil = niveles_gamma(cadena, spot, rate=rate)
    opex = estado_opex(hoy)

    # Los top-N de muros son listas: viven en el JSON, no en el CSV tabular.
    tops = {k: niveles.pop(k) for k in ("call_walls_top", "put_walls_top")}

    registro = {"fecha": hoy.isoformat(), "simbolo": simbolo, **niveles, **{
        k: opex[k] for k in ("proximo_opex", "dias_a_opex", "opex_trimestral", "pre_opex", "ventana_debilidad")
    }}

    # JSON completo del dia: incluye el perfil de GEX y el desglose por strike,
    # que es lo que permite dibujar el grafico y auditar el calculo.
    detalle = {**registro, **tops}
    detalle["perfil_gex"] = perfil.to_dict(orient="records")
    detalle["gex_por_strike"] = (
        con_gex.groupby(["strike", "tipo"])["gex"].sum().reset_index().to_dict(orient="records")
    )
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / f"gex_{simbolo}_{hoy.isoformat()}.json").write_text(
        json.dumps(detalle, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return registro, tops


def escribir_salidas(registros: list[dict], outdir: Path, dir_mt5: Path | None = None):
    df = pd.DataFrame(registros)

    # Fichero plano para MT5: se sobreescribe cada dia con la foto actual.
    mt5 = df.reindex(columns=COLUMNAS_MT5)
    mt5.to_csv(outdir / "gamma_levels.csv", index=False, encoding="utf-8")

    # Copia a la carpeta comun de MetaQuotes, que es de donde lee el indicador.
    if dir_mt5 is not None:
        dir_mt5.mkdir(parents=True, exist_ok=True)
        mt5.to_csv(dir_mt5 / "gamma_levels.csv", index=False, encoding="utf-8")

    # Historico acumulado, sin duplicar fecha+simbolo.
    hist_path = outdir / "gex_history.csv"
    if hist_path.exists():
        previo = pd.read_csv(hist_path)
        df = pd.concat([previo, df], ignore_index=True)
        df = df.drop_duplicates(subset=["fecha", "simbolo"], keep="last")
    df.to_csv(hist_path, index=False, encoding="utf-8")


def resumen(reg: dict, tops: dict) -> str:
    def lista(muros):
        if not muros:
            return "n/d"
        return "  ".join(f"{m['strike']:,.0f} ({m['dist_pct']:+.1f}%, {m['gex']/1e9:+.2f}B)" for m in muros)

    flip = "n/d" if reg["gamma_flip"] is None else f"{reg['gamma_flip']:,.2f}"
    dist = "n/d" if reg["dist_flip_pct"] is None else f"{reg['dist_flip_pct']:+.2f}%"
    return (
        f"{reg['simbolo']}  spot {reg['spot']:,.2f}\n"
        f"  GEX neto     : {reg['gex_neto']/1e9:+.2f} B$ por 1%   -> regimen GAMMA {reg['regimen'].upper()}\n"
        f"  Gamma flip   : {flip}  (spot esta {dist} respecto al flip)\n"
        f"  Call walls   : {lista(tops.get('call_walls_top'))}\n"
        f"  Put walls    : {lista(tops.get('put_walls_top'))}\n"
        f"  OPEX         : proximo {reg['proximo_opex']} (faltan {reg['dias_a_opex']} dias"
        f"{', TRIMESTRAL' if reg['opex_trimestral'] else ''})"
        f"{'  [PRE-OPEX]' if reg['pre_opex'] else ''}"
        f"{'  [VENTANA DEBILIDAD]' if reg['ventana_debilidad'] else ''}\n"
        f"  Contratos    : {reg['n_contratos']:,}  OI total {reg['oi_total']:,}"
    )


def main():
    ap = argparse.ArgumentParser(description="GEX diario desde yfinance")
    ap.add_argument("--simbolos", nargs="+", default=["SPY", "QQQ"])
    ap.add_argument("--max-dias", type=int, default=45, help="vencimientos a incluir")
    ap.add_argument("--rate", type=float, default=0.04, help="tipo libre de riesgo")
    ap.add_argument("--fecha", help="YYYY-MM-DD (por defecto hoy)")
    ap.add_argument("--outdir", default=str(OUT))
    ap.add_argument(
        "--dir-mt5",
        default=str(MT5_COMUN),
        help="carpeta comun de MetaQuotes donde publicar gamma_levels.csv ('' para no copiar)",
    )
    args = ap.parse_args()

    hoy = dt.date.fromisoformat(args.fecha) if args.fecha else dt.date.today()
    outdir = Path(args.outdir)
    dir_mt5 = Path(args.dir_mt5) if args.dir_mt5 else None

    registros = []
    for sim in args.simbolos:
        try:
            reg, tops = procesar(sim, hoy, args.max_dias, args.rate, outdir)
        except Exception as e:  # un simbolo caido no debe tumbar el resto
            print(f"[ERROR] {sim}: {e}")
            continue
        registros.append(reg)
        print(resumen(reg, tops))
        print()

    if registros:
        escribir_salidas(registros, outdir, dir_mt5)
        print(f"Salidas en {outdir}")
        if dir_mt5:
            print(f"gamma_levels.csv publicado en {dir_mt5}")


if __name__ == "__main__":
    main()
