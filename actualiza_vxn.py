# actualiza_vxn.py — regenera VXN_regimen.csv con el histórico completo del ^VXN.
#
# Regla del régimen (validada en el proyecto Connors RSI2 Nasdaq, sep-2026):
#   reg = 1 si el cierre del VXN está por debajo de su SMA de 200 DÍAS DE
#   CALENDARIO (serie rellenada con ffill, findes incluidos); 0 si no.
#
# Cada ejecución reescribe el CSV COMPLETO (yfinance da todo el histórico),
# así que un día sin correr no pierde nada. Lo lanza GitHub Actions a diario.
#
# Formato de salida (ASCII, separador ';'), una fila por día de calendario:
#   YYYY.MM.DD;cierre;sma200;reg
import pandas as pd
import yfinance as yf

SMA = 200
SALIDA = "VXN_regimen.csv"


def main():
    px = yf.download("^VXN", start="2001-01-01", auto_adjust=False, progress=False)
    if isinstance(px.columns, pd.MultiIndex):
        px.columns = px.columns.get_level_values(0)
    s = px["Close"].dropna()
    s.index = pd.to_datetime(s.index).tz_localize(None)
    cal = s.reindex(pd.date_range(s.index.min(), pd.Timestamp.today().normalize())).ffill()
    sma = cal.rolling(SMA).mean()
    df = pd.DataFrame({"close": cal, "sma": sma}).dropna()
    df["reg"] = (df["close"] < df["sma"]).astype(int)

    lineas = ["fecha;vxn;sma200;reg"]
    for fecha, fila in df.iterrows():
        lineas.append(f"{fecha:%Y.%m.%d};{fila['close']:.2f};{fila['sma']:.2f};{int(fila['reg'])}")
    with open(SALIDA, "w", encoding="ascii", newline="\n") as f:
        f.write("\n".join(lineas) + "\n")

    ult = df.index[-1]
    print(f"OK {SALIDA}: {len(df)} filas · última {ult:%Y-%m-%d} · VXN {df['close'].iloc[-1]:.2f} "
          f"vs SMA200cal {df['sma'].iloc[-1]:.2f} · reg={int(df['reg'].iloc[-1])}")


if __name__ == "__main__":
    main()
