"""Descarga y normalizacion de la cadena de opciones (fuente: yfinance).

Aisla el resto del proyecto de la fuente de datos: gex_core.py solo ve un
DataFrame con las columnas de CHAIN_COLUMNS, venga de yfinance hoy o de un feed
de pago manana.

Limitaciones conocidas de yfinance (importantes, no cosmeticas):
  - El open interest es el de la SESION ANTERIOR (la OCC lo publica por la
    manana). El volumen si es del dia.
  - No hay historico: la cadena de ayer no se puede recuperar. Por eso cada
    ejecucion guarda un snapshot en parquet: es la unica forma de construir el
    historico con el que validar el filtro mas adelante.
  - La IV que publica Yahoo es la de su propio modelo y puede venir a 0 o
    ausente en strikes ilíquidos; esas filas se descartan.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import yfinance as yf

# Rango de IV aceptable. Fuera de el, el dato de Yahoo es basura (strikes sin
# mercado que arrastran gammas absurdas).
IV_MIN, IV_MAX = 0.01, 5.0


def spot_actual(ticker: yf.Ticker) -> float:
    """Ultimo precio del subyacente, con fallback al cierre diario."""
    try:
        precio = float(ticker.fast_info["lastPrice"])
        if precio > 0:
            return precio
    except Exception:
        pass
    hist = ticker.history(period="5d")
    if hist.empty:
        raise RuntimeError("No se pudo obtener el spot del subyacente")
    return float(hist["Close"].iloc[-1])


def descargar_cadena(simbolo: str, max_dias=45, hoy: dt.date | None = None) -> tuple[pd.DataFrame, float]:
    """Descarga la cadena completa hasta max_dias de vencimiento.

    Devuelve (cadena_normalizada, spot). La cadena lleva las columnas de
    gex_core.CHAIN_COLUMNS mas 'vencimiento', 'volumen' y 'contrato' para poder
    guardar snapshots utiles.
    """
    hoy = hoy or dt.date.today()
    tk = yf.Ticker(simbolo)
    spot = spot_actual(tk)

    vencimientos = [dt.date.fromisoformat(v) for v in tk.options]
    vencimientos = [v for v in vencimientos if 0 <= (v - hoy).days <= max_dias]
    if not vencimientos:
        raise RuntimeError(f"{simbolo}: sin vencimientos dentro de {max_dias} dias")

    trozos = []
    for venc in vencimientos:
        chain = tk.option_chain(venc.isoformat())
        for tipo, df in (("call", chain.calls), ("put", chain.puts)):
            if df is None or df.empty:
                continue
            t = pd.DataFrame(
                {
                    "tipo": tipo,
                    "vencimiento": venc.isoformat(),
                    "strike": df["strike"].astype(float),
                    "open_interest": pd.to_numeric(df.get("openInterest"), errors="coerce").fillna(0),
                    "volumen": pd.to_numeric(df.get("volume"), errors="coerce").fillna(0),
                    "iv": pd.to_numeric(df.get("impliedVolatility"), errors="coerce"),
                    "contrato": df.get("contractSymbol"),
                }
            )
            t["t_years"] = max((venc - hoy).days, 0) / 365.0
            trozos.append(t)

    cadena = pd.concat(trozos, ignore_index=True)
    return limpiar(cadena), spot


def limpiar(cadena: pd.DataFrame) -> pd.DataFrame:
    """Descarta contratos sin OI o con IV fuera de rango."""
    c = cadena.copy()
    c["open_interest"] = c["open_interest"].astype(float)
    valido = (c["open_interest"] > 0) & c["iv"].between(IV_MIN, IV_MAX)
    return c[valido].reset_index(drop=True)


def guardar_snapshot(cadena: pd.DataFrame, spot: float, simbolo: str, carpeta: Path, hoy: dt.date) -> Path:
    """Guarda la cadena cruda del dia en parquet (historico hacia delante)."""
    carpeta.mkdir(parents=True, exist_ok=True)
    destino = carpeta / f"chain_{simbolo}_{hoy.isoformat()}.parquet"
    snap = cadena.copy()
    snap["simbolo"] = simbolo
    snap["spot"] = spot
    snap["fecha_snapshot"] = hoy.isoformat()
    snap.to_parquet(destino, index=False)
    return destino
