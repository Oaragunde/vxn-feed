# vxn-feed

Feeds diarios de régimen de volatilidad implícita, publicados como CSV para que los EAs de
MetaTrader 5 los consuman vía `WebRequest` (útil en VPS, donde no hay tareas locales).

| feed | índice | lo consume |
|---|---|---|
| `VXN_regimen.csv` | ^VXN (Nasdaq-100) | `EA_ConnorsRSI2Nasdaq_Fut` (Micro MNQ) |
| `VIX_regimen.csv` | ^VIX (S&P 500) | `EA_ConnorsRSI2SP_Fut` (Micro MES) |

Formato de ambos: una fila por **día de calendario**, `fecha;indice;sma200;reg`, donde
`reg=1` significa que el índice está por debajo de su SMA de **200 días de calendario**
(serie rellenada con ffill, findes incluidos) = régimen de volatilidad baja, en el que las
estrategias tienen permitido entrar.

> La SMA es de días de calendario, **no de sesiones**. Calcularla sobre sesiones (200 cierres
> ≈ 286 días) voltea las señales que caen justo en el filo y cambia los resultados. No
> "simplificar" esto.

- `actualiza_vxn.py` / `actualiza_vix.py` — regeneran cada CSV completo desde yfinance.
  Cada ejecución reescribe todo el histórico, así que un día sin correr no pierde nada.
- GitHub Actions los ejecuta de lunes a viernes a las 21:30 UTC.

URLs crudas para los EAs:

```
https://raw.githubusercontent.com/Oaragunde/vxn-feed/main/VXN_regimen.csv
https://raw.githubusercontent.com/Oaragunde/vxn-feed/main/VIX_regimen.csv
```

Para que MT5 pueda descargarlos hay que añadir `https://raw.githubusercontent.com` a la lista
blanca de WebRequest (Herramientas → Opciones → Asesores Expertos).
