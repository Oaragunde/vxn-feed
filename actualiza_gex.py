# actualiza_gex.py — snapshot diario de la cadena de opciones SPY/QQQ + niveles de gamma.
#
# Alimenta al indicador GammaOptionsLevels (filtro de régimen de gamma de Axia para las
# estrategias de ES/MES y NQ/MNQ). A DIFERENCIA de VXN/VIX/GVZ, este feed NO se puede
# regenerar: yfinance solo da la cadena de HOY, así que cada sesión que no se captura
# se pierde para siempre. Por eso vive aquí y no en el PC, que a veces está apagado.
#
# El cálculo es copia literal de tools/gamma_options/ del repo de estrategias (carpeta gex/).
#
# Salvaguardas, porque un snapshot malo es peor que ninguno:
#   1) Solo corre en la ventana TRAS EL CIERRE de una sesión NYSE (16:30-19:30 hora de
#      Nueva York). Antes sería una foto intradía; después Yahoo degrada bid/ask e IV.
#      El workflow dispara dos veces (verano/invierno) y este filtro deja pasar una.
#   2) Si el snapshot del día ya existe, no hace nada (la segunda ejecución del verano).
#   3) Comprueba que cada cadena traiga un mínimo de contratos válidos; si no, falla en
#      rojo (GitHub avisa por correo) en vez de publicar basura.
#
# Uso:
#   python actualiza_gex.py            -> ejecución normal (la del workflow)
#   python actualiza_gex.py --prueba   -> ignora la ventana y escribe en una carpeta
#                                         temporal: prueba el entorno sin tocar el histórico
import argparse
import datetime as dt
import subprocess
import sys
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

RAIZ = Path(__file__).resolve().parent
GEX = RAIZ / "gex"
sys.path.insert(0, str(GEX))

from opex_calendar import en_ventana_post_cierre  # noqa: E402

SIMBOLOS = ["SPY", "QQQ"]
MIN_CONTRATOS = 1000   # SPY y QQQ traen ~3.000-4.500 contratos válidos a 45 días


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prueba", action="store_true")
    args = ap.parse_args()

    ahora_ny = dt.datetime.now(ZoneInfo("America/New_York"))
    fecha = ahora_ny.date()

    if args.prueba:
        outdir = Path(tempfile.mkdtemp(prefix="gex_prueba_"))
        print(f"MODO PRUEBA: salida en {outdir}, no se toca el histórico")
    else:
        if not en_ventana_post_cierre(ahora_ny):
            print(f"{ahora_ny:%Y-%m-%d %H:%M} NY: fuera de la ventana post-cierre, no se captura")
            return
        outdir = GEX
        ya = [s for s in SIMBOLOS if (GEX / "snapshots" / f"chain_{s}_{fecha}.parquet").exists()]
        if len(ya) == len(SIMBOLOS):
            print(f"Snapshot del {fecha} ya capturado, nada que hacer")
            return

    subprocess.run(
        [sys.executable, str(GEX / "gex_daily.py"), "--simbolos", *SIMBOLOS,
         "--fecha", fecha.isoformat(), "--outdir", str(outdir), "--dir-mt5", ""],
        check=True,
    )

    # gex_daily.py no revienta si falla un símbolo (para no tumbar el otro): la
    # comprobación de que el día quedó completo y sano se hace aquí.
    errores = []
    for s in SIMBOLOS:
        snap = outdir / "snapshots" / f"chain_{s}_{fecha}.parquet"
        if not snap.exists():
            errores.append(f"{s}: sin snapshot")
            continue
        n = len(pd.read_parquet(snap))
        if n < MIN_CONTRATOS:
            errores.append(f"{s}: solo {n} contratos válidos (mínimo {MIN_CONTRATOS})")
    if errores:
        sys.exit("SNAPSHOT INCOMPLETO -> " + " | ".join(errores))
    print(f"Snapshot del {fecha} OK")


if __name__ == "__main__":
    main()
