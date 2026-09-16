"""Calendario de vencimientos de opciones (OPEX) y ventana de debilidad.

Es la pieza mas barata de toda la capa de gamma: no necesita datos de opciones,
solo el calendario. Da dos flags que las estrategias pueden consultar:

    - pre_opex          : el precio tiende a quedarse encajonado alrededor de los
                          strikes con mas interes abierto (efecto iman).
    - ventana_debilidad : tras el OPEX mensual el GEX cae en picado y el mercado
                          queda libre del anclaje -> desplazamientos mas limpios.

OPEX mensual = tercer viernes del mes (jueves si ese viernes es festivo, que en
la practica solo ocurre con el Viernes Santo). Los meses de marzo, junio,
septiembre y diciembre son ademas trimestrales ("triple witching"): el
vencimiento mas grande y el que mas GEX retira del mercado.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json


def _pascua(anio: int) -> dt.date:
    """Domingo de Pascua (algoritmo gregoriano anonimo)."""
    a = anio % 19
    b, c = divmod(anio, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes, dia = divmod(h + l - 7 * m + 114, 31)
    return dt.date(anio, mes, dia + 1)


def _festivos_nyse(anio: int) -> set[dt.date]:
    """Festivos de mercado US que caen en dia habil y pueden mover el OPEX.

    Solo hacen falta los que pueden coincidir con un tercer viernes: en la
    practica el Viernes Santo (Good Friday). El resto se incluye para poder
    contar sesiones habiles en la ventana de debilidad.
    """
    fest = {
        dt.date(anio, 1, 1),      # Ano nuevo
        dt.date(anio, 6, 19),     # Juneteenth
        dt.date(anio, 7, 4),      # Independencia
        dt.date(anio, 12, 25),    # Navidad
        _pascua(anio) - dt.timedelta(days=2),  # Viernes Santo
    }
    # Lunes de MLK (3er lunes enero), Presidents (3er lunes febrero),
    # Memorial (ultimo lunes mayo), Labor (1er lunes septiembre),
    # Thanksgiving (4o jueves noviembre).
    fest.add(_n_esimo_dia_semana(anio, 1, 0, 3))
    fest.add(_n_esimo_dia_semana(anio, 2, 0, 3))
    fest.add(_ultimo_dia_semana(anio, 5, 0))
    fest.add(_n_esimo_dia_semana(anio, 9, 0, 1))
    fest.add(_n_esimo_dia_semana(anio, 11, 3, 4))
    return fest


def _n_esimo_dia_semana(anio: int, mes: int, dia_semana: int, n: int) -> dt.date:
    """N-esimo <dia_semana> del mes (lunes=0 ... domingo=6)."""
    d = dt.date(anio, mes, 1)
    desfase = (dia_semana - d.weekday()) % 7
    return d + dt.timedelta(days=desfase + 7 * (n - 1))


def _ultimo_dia_semana(anio: int, mes: int, dia_semana: int) -> dt.date:
    d = dt.date(anio, mes, 28)
    while d.month == mes:
        siguiente = d + dt.timedelta(days=1)
        if siguiente.month != mes:
            break
        d = siguiente
    while d.weekday() != dia_semana:
        d -= dt.timedelta(days=1)
    return d


def opex_mensual(anio: int, mes: int) -> dt.date:
    """Tercer viernes del mes, adelantado al jueves si es festivo."""
    fecha = _n_esimo_dia_semana(anio, mes, 4, 3)
    if fecha in _festivos_nyse(anio):
        fecha -= dt.timedelta(days=1)
    return fecha


def es_trimestral(fecha: dt.date) -> bool:
    """True si el OPEX de ese mes es trimestral (triple witching)."""
    return fecha.month in (3, 6, 9, 12)


def proximo_opex(hoy: dt.date) -> dt.date:
    """Siguiente OPEX mensual >= hoy."""
    candidato = opex_mensual(hoy.year, hoy.month)
    if candidato >= hoy:
        return candidato
    siguiente = hoy.replace(day=1) + dt.timedelta(days=32)
    return opex_mensual(siguiente.year, siguiente.month)


def ultimo_opex(hoy: dt.date) -> dt.date:
    """OPEX mensual anterior o igual a hoy."""
    candidato = opex_mensual(hoy.year, hoy.month)
    if candidato <= hoy:
        return candidato
    anterior = hoy.replace(day=1) - dt.timedelta(days=1)
    return opex_mensual(anterior.year, anterior.month)


def es_sesion_nyse(fecha: dt.date) -> bool:
    """True si ese dia abre el mercado US (laborable y no festivo)."""
    return fecha.weekday() < 5 and fecha not in _festivos_nyse(fecha.year)


def en_ventana_post_cierre(ahora_ny: dt.datetime, desde=(16, 30), hasta=(19, 30)) -> bool:
    """True si la hora de Nueva York cae en la ventana tras el cierre de una sesion.

    Es la unica franja en la que la cadena de yfinance es la del cierre del dia:
    antes el mercado sigue abierto y la foto seria intradia; mucho despues Yahoo
    empieza a vaciar bid/ask y la IV de muchos strikes se degrada.
    """
    if not es_sesion_nyse(ahora_ny.date()):
        return False
    minutos = ahora_ny.hour * 60 + ahora_ny.minute
    return desde[0] * 60 + desde[1] <= minutos <= hasta[0] * 60 + hasta[1]


def sesiones_habiles(desde: dt.date, hasta: dt.date) -> int:
    """Numero de sesiones de mercado entre dos fechas (excluye desde, incluye hasta)."""
    if hasta <= desde:
        return 0
    fest = _festivos_nyse(desde.year) | _festivos_nyse(hasta.year)
    n = 0
    d = desde + dt.timedelta(days=1)
    while d <= hasta:
        if d.weekday() < 5 and d not in fest:
            n += 1
        d += dt.timedelta(days=1)
    return n


def estado_opex(hoy: dt.date | None = None, dias_pre=5, sesiones_ventana=5) -> dict:
    """Flags de contexto OPEX para una fecha.

    dias_pre         : dias naturales antes del OPEX en que se activa el iman.
    sesiones_ventana : sesiones tras el OPEX que dura la ventana de debilidad.
    """
    hoy = hoy or dt.date.today()
    prox = proximo_opex(hoy)
    ult = ultimo_opex(hoy)

    dias_a_opex = (prox - hoy).days
    sesiones_desde_opex = sesiones_habiles(ult, hoy)

    return {
        "fecha": hoy.isoformat(),
        "proximo_opex": prox.isoformat(),
        "dias_a_opex": dias_a_opex,
        "opex_trimestral": es_trimestral(prox),
        "ultimo_opex": ult.isoformat(),
        "sesiones_desde_opex": sesiones_desde_opex,
        "es_opex": hoy == prox,
        "pre_opex": 0 <= dias_a_opex <= dias_pre,
        "ventana_debilidad": 0 < sesiones_desde_opex <= sesiones_ventana,
    }


def main():
    ap = argparse.ArgumentParser(description="Estado del calendario OPEX")
    ap.add_argument("--fecha", help="YYYY-MM-DD (por defecto hoy)")
    ap.add_argument("--dias-pre", type=int, default=5)
    ap.add_argument("--sesiones-ventana", type=int, default=5)
    args = ap.parse_args()

    hoy = dt.date.fromisoformat(args.fecha) if args.fecha else dt.date.today()
    print(json.dumps(estado_opex(hoy, args.dias_pre, args.sesiones_ventana), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
