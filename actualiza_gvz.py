# actualiza_gvz.py — regenera GVZ_regimen.csv con el histórico completo del ^GVZ.
#
# Alimenta al EA_OroTardeVAH_Fut (Oro Tarde VAH sobre Micro Gold MGC). Es gemelo de
# actualiza_vix.py en el esquema (yfinance, CSV completo, GitHub Actions a diario), pero
# DIFIERE EN TRES COSAS que no hay que "unificar", porque así se validó la estrategia:
#
#   1) SMA de 200 SESIONES (filas de yfinance), NO de 200 días de calendario.
#   2) Una fila por SESIÓN del GVZ, NO por día de calendario. El EA busca la última fila con
#      fecha <= día de señal y usa la ANTERIOR: si hubiera filas de fin de semana rellenadas,
#      esa "anterior" sería otra y el régimen cambiaría.
#   3) reg = 1 si el cierre está POR ENCIMA de la SMA (volatilidad ALTA = puede operar).
#      En VXN y VIX es al revés porque el Connors compra pánico; Oro Tarde es continuación.
#
# Salida idéntica a tools/gamma_options/gvz_daily.py del repo de estrategias (verificada al
# publicar este feed). Formato ASCII, separador ';':
#   YYYY.MM.DD;cierre;sma200;reg
import pandas as pd
import yfinance as yf

SMA = 200
SALIDA = "GVZ_regimen.csv"


def main():
    px = yf.download("^GVZ", start="2001-01-01", auto_adjust=False, progress=False)
    if isinstance(px.columns, pd.MultiIndex):
        px.columns = px.columns.get_level_values(0)
    s = px["Close"].dropna()
    s.index = pd.to_datetime(s.index).tz_localize(None)
    sma = s.rolling(SMA).mean()                      # 200 SESIONES
    df = pd.DataFrame({"close": s, "sma": sma}).dropna()
    df["reg"] = (df["close"] > df["sma"]).astype(int)  # OJO: > (vol alta)

    lineas = ["fecha;gvz;sma200;reg"]
    for fecha, fila in df.iterrows():
        lineas.append(f"{fecha:%Y.%m.%d};{fila['close']:.2f};{fila['sma']:.2f};{int(fila['reg'])}")
    with open(SALIDA, "w", encoding="ascii", newline="\n") as f:
        f.write("\n".join(lineas) + "\n")

    ult = df.index[-1]
    print(f"OK {SALIDA}: {len(df)} sesiones · última {ult:%Y-%m-%d} · GVZ {df['close'].iloc[-1]:.2f} "
          f"vs SMA200 sesiones {df['sma'].iloc[-1]:.2f} · reg={int(df['reg'].iloc[-1])}")


if __name__ == "__main__":
    main()
