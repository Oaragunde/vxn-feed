# vxn-feed

Feeds diarios de régimen de volatilidad implícita, publicados como CSV para que los EAs de
MetaTrader 5 los consuman vía `WebRequest` (útil en VPS, donde no hay tareas locales).

| feed | índice | lo consume |
|---|---|---|
| `VXN_regimen.csv` | ^VXN (Nasdaq-100) | `EA_ConnorsRSI2Nasdaq_Fut` (Micro MNQ) |
| `VIX_regimen.csv` | ^VIX (S&P 500) | `EA_ConnorsRSI2SP_Fut` (Micro MES) |
| `GVZ_regimen.csv` | ^GVZ (oro) | `EA_OroTardeVAH_Fut` (Micro Gold MGC) — **formato distinto, ver abajo** |
| `gex/gamma_levels.csv` | cadena de opciones SPY/QQQ | indicador `GammaOptionsLevels` (filtro de gamma, ES/NQ) — **no regenerable, ver abajo** |

Formato de VXN y VIX: una fila por **día de calendario**, `fecha;indice;sma200;reg`, donde
`reg=1` significa que el índice está por debajo de su SMA de **200 días de calendario**
(serie rellenada con ffill, findes incluidos) = régimen de volatilidad baja, en el que las
estrategias tienen permitido entrar.

> La SMA es de días de calendario, **no de sesiones**. Calcularla sobre sesiones (200 cierres
> ≈ 286 días) voltea las señales que caen justo en el filo y cambia los resultados. No
> "simplificar" esto.

### GVZ: formato distinto a propósito

`GVZ_regimen.csv` se validó con otra regla y **no debe igualarse** a los otros dos:

1. SMA de **200 sesiones** (filas de yfinance), no de 200 días de calendario.
2. **Una fila por sesión** del GVZ, no por día de calendario. El EA busca la última fila con
   fecha anterior o igual al día de la señal y usa la **anterior** a ésa; con filas de fin de
   semana rellenadas, esa "anterior" sería otra y el régimen cambiaría.
3. `reg=1` si el GVZ está **por encima** de su SMA (volatilidad alta = puede entrar). Oro Tarde
   VAH es continuación alcista; el Connors, que compra pánico, usa lo contrario.

Al publicarlo se comprobó que reproduce exactamente el CSV con el que se validó la estrategia
(4.395 sesiones, 0 diferencias).

- `actualiza_vxn.py` / `actualiza_vix.py` / `actualiza_gvz.py` — regeneran cada CSV completo
  desde yfinance. Cada ejecución reescribe todo el histórico, así que un día sin correr no
  pierde nada.
- GitHub Actions los ejecuta de lunes a viernes a las 21:30 UTC, con un repaso a las 23:45 UTC
  (en invierno los índices cierran a las 21:15 UTC y a las 21:30 el cierre aún puede ser provisional).

URLs crudas para los EAs:

```
https://raw.githubusercontent.com/Oaragunde/vxn-feed/main/VXN_regimen.csv
https://raw.githubusercontent.com/Oaragunde/vxn-feed/main/VIX_regimen.csv
https://raw.githubusercontent.com/Oaragunde/vxn-feed/main/GVZ_regimen.csv
```

Para que MT5 pueda descargarlos hay que añadir `https://raw.githubusercontent.com` a la lista
blanca de WebRequest (Herramientas → Opciones → Asesores Expertos).

## GEX: snapshot de opciones SPY/QQQ (carpeta `gex/`)

**Distinto a todo lo anterior en lo más importante: no se puede regenerar.** yfinance solo da
la cadena de opciones de hoy, así que cada sesión que no se captura se pierde para siempre.
Por eso se mudó aquí desde la tarea del PC (que perdió más de la mitad de los días).

- `actualiza_gex.py` + `.github/workflows/gex.yml`: L-V a las **22:45 hora peninsular** todo el
  año (dos cron en UTC; el script solo deja pasar la que cae en la ventana post-cierre
  16:30-19:30 de Nueva York y no repite si el día ya está).
- Falla en rojo si alguna cadena trae menos de 1.000 contratos válidos: fuera de esa ventana
  Yahoo degrada la cadena (a las 06:00 NY devuelve 34 contratos en SPY).
- `gex/gex_core.py`, `chain_source.py`, `opex_calendar.py`, `gex_daily.py`: copia literal de
  `tools/gamma_options/` del repo de estrategias. **Si se cambia allí, copiar aquí.**
- Salidas: `gex/snapshots/chain_<SIM>_<fecha>.parquet` (cadena cruda, el histórico que
  importa), `gex/gex_history.csv` (niveles por sesión) y `gex/gamma_levels.csv` (último día).
- La **fecha es la de la sesión de Nueva York**. Al migrar se corrigieron tres snapshots del PC
  que llevaban la fecha española (ver `gex/snapshots_dudosos/LEEME.md`).
- Prueba del entorno sin tocar el histórico: *Actions → Snapshot GEX → Run workflow* (prueba = sí).

```
https://raw.githubusercontent.com/Oaragunde/vxn-feed/main/gex/gamma_levels.csv
```
