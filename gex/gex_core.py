"""Nucleo de calculo de GEX (Gamma Exposure) a partir de una cadena de opciones.

Funciones puras, sin red ni ficheros: se les pasa un DataFrame de cadena ya
normalizado y devuelven los niveles institucionales que usa Axia:

    - GEX neto (dolares de gamma por cada 1% de movimiento del subyacente)
    - Gamma Flip  : nivel de spot donde el GEX neto cruza cero
    - Call Wall   : strike con mayor gamma de calls POR ENCIMA del spot
    - Put Wall    : strike con mayor gamma de puts POR DEBAJO del spot
    - Regimen     : "positivo" (dealers amortiguan) / "negativo" (amplifican)

Convencion dealer estandar (la que usan SpotGamma/MenthorQ y la que describe el
informe de Axia): el publico COMPRA calls y VENDE puts contra los dealers, luego
el dealer queda LARGO de gamma en las calls (+) y CORTO en las puts (-).
Es una aproximacion: no sabemos quien esta a cada lado de cada contrato.

El sesgo horario y la calidad del open interest dependen de la fuente de datos
(ver gex_daily.py); aqui solo esta la matematica.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Columnas que espera todo el modulo en el DataFrame de cadena.
CHAIN_COLUMNS = ["tipo", "strike", "open_interest", "iv", "t_years"]

# Multiplicador de contrato de opciones sobre acciones/ETF en EEUU.
CONTRACT_MULTIPLIER = 100

# Suelo de vida residual: una opcion que vence hoy tiene T=0 y la gamma
# explotaria a infinito. Medio dia de calendario es suficiente para acotarla.
MIN_T_YEARS = 0.5 / 365.0


def bs_gamma(spot, strike, iv, t_years, rate=0.04, div_yield=0.0):
    """Gamma de Black-Scholes (identica para call y put).

    gamma = exp(-q*T) * phi(d1) / (S * sigma * sqrt(T))

    Acepta escalares o arrays de numpy. Devuelve 0 donde los inputs no son
    validos (IV<=0, T<=0) en lugar de propagar NaN.
    """
    spot = np.asarray(spot, dtype=float)
    strike = np.asarray(strike, dtype=float)
    iv = np.asarray(iv, dtype=float)
    t_years = np.maximum(np.asarray(t_years, dtype=float), MIN_T_YEARS)

    valido = (spot > 0) & (strike > 0) & (iv > 0) & (t_years > 0)
    # Sustituimos los invalidos por valores inocuos para evitar warnings de numpy
    # y los anulamos al final.
    iv_s = np.where(valido, iv, 1.0)
    spot_s = np.where(valido, spot, 1.0)
    strike_s = np.where(valido, strike, 1.0)

    sqrt_t = np.sqrt(t_years)
    d1 = (np.log(spot_s / strike_s) + (rate - div_yield + 0.5 * iv_s**2) * t_years) / (iv_s * sqrt_t)
    phi = np.exp(-0.5 * d1**2) / np.sqrt(2.0 * np.pi)
    gamma = np.exp(-div_yield * t_years) * phi / (spot_s * iv_s * sqrt_t)

    return np.where(valido, gamma, 0.0)


def gex_por_contrato(spot, gamma, open_interest, tipo):
    """Dolares de gamma por cada 1% de movimiento del subyacente, con signo dealer.

    GEX = gamma * OI * multiplicador * spot^2 * 0.01, positivo en calls y
    negativo en puts.
    """
    signo = np.where(np.asarray(tipo) == "call", 1.0, -1.0)
    bruto = gamma * np.asarray(open_interest, dtype=float) * CONTRACT_MULTIPLIER
    return signo * bruto * np.asarray(spot, dtype=float) ** 2 * 0.01


def calcular_gex(cadena: pd.DataFrame, spot: float, rate=0.04, div_yield=0.0) -> pd.DataFrame:
    """Anade columnas gamma y gex a la cadena, evaluadas al spot actual."""
    faltan = [c for c in CHAIN_COLUMNS if c not in cadena.columns]
    if faltan:
        raise ValueError(f"La cadena no trae las columnas requeridas: {faltan}")

    out = cadena.copy()
    out["gamma"] = bs_gamma(spot, out["strike"], out["iv"], out["t_years"], rate, div_yield)
    out["gex"] = gex_por_contrato(spot, out["gamma"].to_numpy(), out["open_interest"], out["tipo"])
    return out


def perfil_gex(cadena: pd.DataFrame, spots, rate=0.04, div_yield=0.0) -> pd.DataFrame:
    """GEX neto recalculado para una rejilla de spots hipoteticos.

    Es lo que permite localizar el Gamma Flip: el strike/OI/IV se mantienen fijos
    y solo se mueve el subyacente, igual que hace SpotGamma.
    """
    filas = []
    for s in np.asarray(spots, dtype=float):
        gamma = bs_gamma(s, cadena["strike"], cadena["iv"], cadena["t_years"], rate, div_yield)
        gex = gex_por_contrato(s, gamma, cadena["open_interest"], cadena["tipo"])
        filas.append({"spot": s, "gex_neto": float(np.sum(gex))})
    return pd.DataFrame(filas)


def gamma_flip(perfil: pd.DataFrame, spot: float) -> float | None:
    """Nivel de spot donde el GEX neto cruza cero, interpolado linealmente.

    Si hay varios cruces devuelve el mas cercano al spot actual, que es el que
    manda para el regimen de hoy. Devuelve None si no hay ningun cruce en la
    rejilla (mercado inequivocamente largo o corto de gamma en todo el rango).
    """
    p = perfil.sort_values("spot").reset_index(drop=True)
    y = p["gex_neto"].to_numpy()
    x = p["spot"].to_numpy()

    cruces = []
    for i in range(len(x) - 1):
        y0, y1 = y[i], y[i + 1]
        if y0 == 0.0:
            cruces.append(float(x[i]))
        elif y0 * y1 < 0:
            # Interpolacion lineal del cero entre los dos puntos.
            cruces.append(float(x[i] + (x[i + 1] - x[i]) * (-y0) / (y1 - y0)))

    if not cruces:
        return None
    return min(cruces, key=lambda c: abs(c - spot))


def muros(cadena_con_gex: pd.DataFrame, spot: float, banda_pct=0.15, top=3) -> dict:
    """Call Wall y Put Wall: strikes con mayor concentracion de gamma a cada lado.

    banda_pct acota la busqueda a un entorno del spot (por defecto +-15%): los
    strikes muy lejanos acumulan OI residual que no imanta el precio a corto.

    Se devuelve tambien el top-N de cada lado porque el maximo suele caer en el
    strike pegado al spot (los ATM del vencimiento proximo acaparan la gamma) y
    ese no es un nivel operable: el muro util suele ser el segundo o el tercero.
    """
    c = cadena_con_gex
    dentro = (c["strike"] >= spot * (1 - banda_pct)) & (c["strike"] <= spot * (1 + banda_pct))

    calls = c[dentro & (c["tipo"] == "call") & (c["strike"] >= spot)]
    puts = c[dentro & (c["tipo"] == "put") & (c["strike"] <= spot)]

    call_por_strike = calls.groupby("strike")["gex"].sum()
    put_por_strike = puts.groupby("strike")["gex"].sum()

    res = {
        "call_wall": None,
        "call_wall_gex": None,
        "put_wall": None,
        "put_wall_gex": None,
        "call_walls_top": [],
        "put_walls_top": [],
    }
    if not call_por_strike.empty:
        mayores = call_por_strike.nlargest(top)
        res["call_wall"] = float(mayores.index[0])
        res["call_wall_gex"] = float(mayores.iloc[0])
        res["call_walls_top"] = [
            {"strike": float(k), "gex": float(v), "dist_pct": float((k - spot) / spot * 100.0)}
            for k, v in mayores.items()
        ]
    if not put_por_strike.empty:
        # Los puts llevan GEX negativo: el muro es el mas negativo (mayor modulo).
        menores = put_por_strike.nsmallest(top)
        res["put_wall"] = float(menores.index[0])
        res["put_wall_gex"] = float(menores.iloc[0])
        res["put_walls_top"] = [
            {"strike": float(k), "gex": float(v), "dist_pct": float((k - spot) / spot * 100.0)}
            for k, v in menores.items()
        ]
    return res


def niveles_gamma(
    cadena: pd.DataFrame,
    spot: float,
    rate=0.04,
    div_yield=0.0,
    banda_flip_pct=0.15,
    pasos_flip=241,
    banda_muros_pct=0.15,
    banda_neutra_pct=0.25,
) -> dict:
    """Calculo completo: GEX neto, gamma flip, muros y regimen.

    Devuelve un dict serializable a JSON, que es lo que consume MT5.
    """
    con_gex = calcular_gex(cadena, spot, rate, div_yield)

    rejilla = np.linspace(spot * (1 - banda_flip_pct), spot * (1 + banda_flip_pct), pasos_flip)
    perfil = perfil_gex(cadena, rejilla, rate, div_yield)
    flip = gamma_flip(perfil, spot)

    gex_neto = float(con_gex["gex"].sum())
    gex_calls = float(con_gex.loc[con_gex["tipo"] == "call", "gex"].sum())
    gex_puts = float(con_gex.loc[con_gex["tipo"] == "put", "gex"].sum())

    # El regimen lo manda el signo del GEX neto al spot actual; el flip es el
    # nivel de referencia para saber cuanto margen hay hasta cambiar de regimen.
    dist_flip_pct = None if flip is None else float((spot - flip) / spot * 100.0)
    regimen = "positivo" if gex_neto > 0 else "negativo"

    # Con el spot pegado al flip el signo del GEX baila de un dia para otro (y de
    # una hora para otra): el regimen no es informacion, es ruido. Se marca
    # INDEFINIDO para que las estrategias no lo usen como filtro.
    if dist_flip_pct is not None and abs(dist_flip_pct) < banda_neutra_pct:
        regimen = "indefinido"

    resultado = {
        "spot": float(spot),
        "gex_neto": gex_neto,
        "gex_calls": gex_calls,
        "gex_puts": gex_puts,
        "gamma_flip": flip,
        "regimen": regimen,
        "dist_flip_pct": dist_flip_pct,
        "n_contratos": int(len(con_gex)),
        "oi_total": int(con_gex["open_interest"].sum()),
    }
    resultado.update(muros(con_gex, spot, banda_muros_pct))
    return resultado, con_gex, perfil
