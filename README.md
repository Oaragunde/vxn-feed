# vxn-feed

Feed diario del régimen de volatilidad implícita del Nasdaq (índice ^VXN),
publicado como CSV para que un EA de MetaTrader 5 lo consuma vía `WebRequest`
(útil en VPS, donde no hay tareas locales).

- `VXN_regimen.csv` — una fila por día de calendario: `fecha;vxn;sma200;reg`
  (`reg=1` = VXN por debajo de su SMA de 200 días de calendario, ffill).
- `actualiza_vxn.py` — regenera el CSV completo desde yfinance.
- GitHub Actions lo ejecuta de lunes a viernes a las 21:30 UTC.

URL cruda para el EA:
`https://raw.githubusercontent.com/Oaragunde/vxn-feed/main/VXN_regimen.csv`
