# -*- coding: utf-8 -*-
"""regimen_feeds.py v3 - feeds de regimen de volatilidad (VXN, VIX, GVZ) para los EAs.

Sustituye a vxn_daily_v2.py / vix_daily_v1.py / gvz_daily.py con TRES cambios de politica
(acordados con Oscar el 20-oct-2026, tras el aviso de CSV del 19/20-oct):

1. NUNCA mas relleno de un dia de mercado. Los generadores antiguos hacian
   `reindex(hasta hoy).ffill()`: si Yahoo aun no tenia el cierre de hoy, escribian una fila de
   hoy con el cierre de AYER. Para el EA esa fila parece un dato real. Aqui un dia de mercado
   sin cierre real NO tiene fila (el EA dice «sin dato» y no opera, que es lo seguro). Solo se
   rellenan (fuente R) los dias SIN mercado (fines de semana y festivos de EE. UU.), que es lo
   que hacia el estudio validado.
2. Fuente de verdad: el historico OFICIAL de Cboe (22-oct-2026, a peticion de Oscar: el valor
   tiene que ser exacto, no aproximado). Donde Cboe tiene el dia (todo el historico y, para VXN y
   GVZ, el cierre de anoche desde ~00:40 UTC) manda Cboe (fuente C). Donde no lo tiene (su VIX va
   un dia por detras) se usa Yahoo (Y) y, si tampoco, TradingView (T, solo VXN y VIX); cuando
   Cboe publica el dia, SUSTITUYE al dato provisional. Reintento cada hora hasta las 07:55.
3. Yahoo y TradingView conviven en el mismo CSV (columnas yahoo y tv) para contrastar; cualquier
   diferencia de Yahoo con Cboe en los ultimos dias queda en los avisos.

Formato (ANSI, ';'), una fila por dia de calendario (VXN, VIX) o por sesion (GVZ):

    fecha;valor;sma200;reg;fuente;yahoo;tv

Las cuatro primeras columnas son las de siempre (los EAs leen p[0] y p[3] y exigen >= 4 campos,
asi que las tres nuevas al final no les afectan). `fuente`: Y = Yahoo, T = TradingView,
C = Cboe (oficial, manda), R = relleno de dia sin mercado. `yahoo` y `tv` guardan cada observacion;
`valor` es la usada.

Reglas del regimen (iguales a las validadas; NO se tocan):
  VXN, VIX: SMA 200 dias de CALENDARIO (serie con ffill), reg = 1 si cierre < SMA.
  GVZ:      SMA 200 SESIONES, reg = 1 si cierre > SMA.

Uso:
  python regimen_feeds.py --out DIR --destino DIR [--destino DIR ...] [--modo una-vez|espera]
  --modo espera: tras la primera pasada reintenta cada --cada minutos (por defecto 60) hasta que
  Yahoo confirme el cierre esperado de todas las series o llegue --hasta (HH:MM locales, 07:55).
Codigo de salida: 0 todo con fila del dia esperado; 1 falta algun cierre esperado; 2 error.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

SMA = 200
TOLERANCIA_PCT = 0.5          # |yahoo - tv| / yahoo, en %, a partir de la cual se avisa
CIERRE_ET_MIN = 16 * 60 + 40  # 16:15 ET es el cierre; 25 min de margen para el dato final
URL_TV = 'https://scanner.tradingview.com/america/scan'
URL_CBOE = 'https://cdn-api.cboe.com/api/global/us_indices/daily_prices/{}_History.csv'
DESDE_CBOE = date(2001, 1, 1)  # el historico de Cboe empieza antes (VIX 1990); se usa lo mismo que el de Yahoo
DIAS_CONTRASTE = 10           # ultimas filas en las que se avisa si Yahoo y Cboe no coinciden
TOLERANCIA_CBOE = 0.005       # Cboe da 2 decimales: cualquier diferencia de un centimo ya es real
CABECERA_COMUN = 'sma200;reg;fuente;yahoo;tv'
FUENTES_VALIDAS = ('Y', 'C', 'T', 'R')


@dataclass(frozen=True)
class Serie:
    nombre: str          # VXN
    yahoo: str           # ^VXN
    tv: str | None       # CBOE:VXN (None = no esta en TradingView)
    calendario: bool     # True: una fila por dia de calendario con ffill de dias sin mercado
    reg_bajo: bool       # True: reg = 1 si cierre < sma (VXN, VIX); False: si cierre > sma (GVZ)

    @property
    def archivo(self) -> str:
        return f'{self.nombre}_regimen.csv'

    @property
    def cabecera(self) -> str:
        return f'fecha;{self.nombre.lower()};{CABECERA_COMUN}'


SERIES = {
    'VXN': Serie('VXN', '^VXN', 'CBOE:VXN', True, True),
    'VIX': Serie('VIX', '^VIX', 'CBOE:VIX', True, True),
    'GVZ': Serie('GVZ', '^GVZ', None, False, False),
}


# ---------------------------------------------------------------- calendario de EE. UU.
def _nesimo_dia_semana(anio: int, mes: int, dia_semana: int, n: int) -> date:
    """n-esimo `dia_semana` (0 = lunes) del mes; n = -1 para el ultimo."""
    if n > 0:
        d = date(anio, mes, 1)
        d += timedelta(days=(dia_semana - d.weekday()) % 7)
        return d + timedelta(weeks=n - 1)
    d = date(anio + (mes == 12), mes % 12 + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - dia_semana) % 7)


def _pascua(anio: int) -> date:
    a, b, c = anio % 19, anio // 100, anio % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    ll = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ll) // 451
    mes = (h + ll - 7 * m + 114) // 31
    dia = (h + ll - 7 * m + 114) % 31 + 1
    return date(anio, mes, dia)


def _observado(d: date) -> date:
    """Festivo fijo: sabado -> viernes anterior, domingo -> lunes siguiente."""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def festivos_usa(anio: int) -> set[date]:
    """Cierres completos de la bolsa de EE. UU. (CBOE no publica VIX/VXN/GVZ esos dias).

    El 1 de enero en sabado NO se traslada al viernes (la bolsa abre el 31-dic)."""
    fest = {
        _nesimo_dia_semana(anio, 1, 0, 3),           # Martin Luther King
        _nesimo_dia_semana(anio, 2, 0, 3),           # Presidents Day
        _pascua(anio) - timedelta(days=2),           # Viernes Santo
        _nesimo_dia_semana(anio, 5, 0, -1),          # Memorial Day
        _nesimo_dia_semana(anio, 9, 0, 1),           # Labor Day
        _nesimo_dia_semana(anio, 11, 3, 4),          # Thanksgiving
        _observado(date(anio, 7, 4)),                # Independence Day
        _observado(date(anio, 12, 25)),              # Navidad
    }
    if anio >= 2022:
        fest.add(_observado(date(anio, 6, 19)))      # Juneteenth
    ano_nuevo = date(anio, 1, 1)
    if ano_nuevo.weekday() != 5:
        fest.add(_observado(ano_nuevo))
    # el 1-ene del anio siguiente en sabado se observaria el 31-dic: no ocurre (ver arriba)
    return fest


def es_dia_de_mercado(d: date) -> bool:
    return d.weekday() < 5 and d not in festivos_usa(d.year)


def a_et(ahora_utc: datetime) -> datetime:
    """Hora de Nueva York (naive) sin depender de tzdata: horario de verano EE. UU. del
    segundo domingo de marzo (07:00 UTC) al primer domingo de noviembre (06:00 UTC)."""
    u = ahora_utc.astimezone(timezone.utc).replace(tzinfo=None)
    ini = datetime.combine(_nesimo_dia_semana(u.year, 3, 6, 2), datetime.min.time()) + timedelta(hours=7)
    fin = datetime.combine(_nesimo_dia_semana(u.year, 11, 6, 1), datetime.min.time()) + timedelta(hours=6)
    return u + timedelta(hours=-4 if ini <= u < fin else -5)


def ultimo_cierre_esperado(ahora_utc: datetime) -> date:
    """Ultimo dia de mercado cuyo cierre ya debe estar publicado (16:40 ET o despues)."""
    et = a_et(ahora_utc)
    d = et.date()
    if et.hour * 60 + et.minute < CIERRE_ET_MIN:
        d -= timedelta(days=1)
    while not es_dia_de_mercado(d):
        d -= timedelta(days=1)
    return d


# ---------------------------------------------------------------- fuentes
def descarga_yahoo(ticker: str):
    """Historico completo de cierres (pandas.Series indexada por fecha naive)."""
    import pandas as pd
    import yfinance as yf
    px = yf.download(ticker, start='2001-01-01', auto_adjust=False, progress=False)
    if isinstance(px.columns, pd.MultiIndex):
        px.columns = px.columns.get_level_values(0)
    s = px['Close'].dropna()
    s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
    return s


@dataclass(frozen=True)
class CierreTV:
    valor: float
    fecha: date          # dia (hora de Nueva York) de la barra diaria que devuelve TradingView


def consulta_tv(simbolos: list[str], abrir=urllib.request.urlopen, intentos: int = 2) -> dict[str, CierreTV]:
    """`close` y `time` (inicio de la barra diaria) de TradingView. Vacio si no responde.

    OJO: `close` es el ultimo precio de la barra en curso. Solo vale como cierre del dia D si
    `fecha` == D (la barra de un dia nuevo, p. ej. el VIX a partir de las 03:15 ET, no es el cierre)."""
    cuerpo = json.dumps({'symbols': {'tickers': simbolos}, 'columns': ['close', 'time']}).encode()
    for k in range(intentos):
        try:
            req = urllib.request.Request(URL_TV, data=cuerpo, headers={
                'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'})
            with abrir(req, timeout=20) as r:
                datos = json.load(r)
            salida = {}
            for fila in datos.get('data', []):
                cierre, hora = fila['d'][0], fila['d'][1]
                if isinstance(cierre, (int, float)) and isinstance(hora, (int, float)) and cierre > 0:
                    f = a_et(datetime.fromtimestamp(hora, timezone.utc)).date()
                    salida[fila['s']] = CierreTV(float(cierre), f)
            return salida
        except Exception as e:  # noqa: BLE001 - red o JSON raro: se reintenta y luego se sigue sin TV
            print(f'AVISO TradingView intento {k + 1}: {e}')
            time.sleep(2)
    return {}


def descarga_cboe(nombre: str, abrir=urllib.request.urlopen, intentos: int = 2) -> dict[date, float]:
    """Cierres oficiales de Cboe (historico CSV publicado por ellos). Vacio si no responde.

    Formato: `DATE,GVZ` (GVZ) o `DATE,OPEN,HIGH,LOW,CLOSE` (VIX, VXN) con fechas MM/DD/YYYY.
    Se guarda todo el historico desde DESDE_CBOE: es la fuente de verdad de la serie."""
    for k in range(intentos):
        try:
            req = urllib.request.Request(URL_CBOE.format(nombre), headers={'User-Agent': 'Mozilla/5.0'})
            with abrir(req, timeout=30) as r:
                texto = r.read().decode('latin-1')
            lineas = texto.replace('\r', '').split('\n')
            cab = [c.strip().upper() for c in lineas[0].split(',')]
            col = cab.index('CLOSE') if 'CLOSE' in cab else 1
            salida = {}
            for linea in lineas[1:]:
                c = linea.split(',')
                if len(c) <= col:
                    continue
                try:
                    f = datetime.strptime(c[0].strip(), '%m/%d/%Y').date()
                    v = float(c[col])
                except ValueError:
                    continue
                if f >= DESDE_CBOE and v == v and v > 0:
                    salida[f] = v
            return salida
        except Exception as e:  # noqa: BLE001 - red o CSV raro: se reintenta y luego se sigue sin Cboe
            print(f'AVISO Cboe {nombre} intento {k + 1}: {e}')
            time.sleep(2)
    return {}


# ---------------------------------------------------------------- lectura del CSV previo
@dataclass
class Previo:
    """Lo que el CSV anterior sabe y el siguiente debe conservar."""
    tv: dict[date, float] = field(default_factory=dict)
    yahoo: dict[date, float] = field(default_factory=dict)
    reales: dict[date, float] = field(default_factory=dict)   # Y, C o T (no R)
    cboe: dict[date, float] = field(default_factory=dict)     # filas cuya fuente fue Cboe
    fuente: dict[date, str] = field(default_factory=dict)


def _fecha(campo: str) -> date | None:
    if len(campo) != 10 or campo[4] != '.' or campo[7] != '.':
        return None
    try:
        return date(int(campo[:4]), int(campo[5:7]), int(campo[8:]))
    except ValueError:
        return None


def _num(campo: str) -> float | None:
    try:
        v = float(campo)
    except ValueError:
        return None
    return v if v == v and v > 0 else None


def lee_previo(texto: str) -> Previo:
    p = Previo()
    for linea in texto.splitlines():
        c = linea.strip().split(';')
        if len(c) < 7:      # formato antiguo de 4 columnas: no aporta nada que conservar
            continue
        f = _fecha(c[0])
        if f is None:
            continue
        valor, fuente = _num(c[1]), c[4]
        if fuente in ('Y', 'C', 'T') and valor is not None:
            p.reales[f] = valor
        if fuente == 'C' and valor is not None:
            p.cboe[f] = valor
        p.fuente[f] = fuente
        y, t = _num(c[5]), _num(c[6])
        if y is not None:
            p.yahoo[f] = y
        if t is not None:
            p.tv[f] = t
    return p


# ---------------------------------------------------------------- construccion del CSV
@dataclass
class Fila:
    fecha: date
    valor: float
    sma: float
    reg: int
    fuente: str
    yahoo: float | None
    tv: float | None

    def texto(self) -> str:
        y = '' if self.yahoo is None else f'{self.yahoo:.2f}'
        t = '' if self.tv is None else f'{self.tv:.2f}'
        return f'{self.fecha:%Y.%m.%d};{self.valor:.2f};{self.sma:.2f};{self.reg};{self.fuente};{y};{t}'


@dataclass
class Resultado:
    serie: Serie
    esperado: date
    filas: list[Fila]
    avisos: list[str] = field(default_factory=list)

    @property
    def ultima_real(self) -> Fila | None:
        reales = [f for f in self.filas if f.fuente in ('Y', 'C', 'T')]
        return reales[-1] if reales else None

    @property
    def fila_esperada(self) -> Fila | None:
        return next((f for f in self.filas if f.fecha == self.esperado and f.fuente in ('Y', 'C', 'T')), None)

    @property
    def estado(self) -> str:
        """OK = el dia esperado tiene cierre de Cboe o de Yahoo; SOLO_TV = solo TradingView; FALTA = ninguno."""
        f = self.fila_esperada
        if f is None:
            return 'FALTA'
        return 'OK' if f.fuente in ('Y', 'C') else 'SOLO_TV'

    def contenido(self) -> str:
        return '\n'.join([self.serie.cabecera] + [f.texto() for f in self.filas]) + '\n'


def construye(serie: Serie, yahoo, tv: CierreTV | None, previo: Previo, ahora_utc: datetime,
              cboe: dict[date, float] | None = None) -> Resultado:
    """Calcula el CSV completo. `yahoo`: pandas.Series (o None si Yahoo no responde).
    `cboe`: historico oficial de Cboe (manda donde tiene el dia; Yahoo y TradingView solo rellenan lo que falta)."""
    import pandas as pd
    esperado = ultimo_cierre_esperado(ahora_utc)
    avisos: list[str] = []

    y: dict[date, float] = {}
    if yahoo is not None:
        for idx, v in yahoo.items():
            d = idx.date()
            if d <= esperado and v == v and v > 0:        # fuera lo provisional de un dia aun abierto
                y[d] = float(v)
    elif previo.reales:
        avisos.append(f'{serie.nombre}: Yahoo no responde; se parte del CSV anterior')
        y = {d: v for d, v in previo.reales.items() if previo.fuente.get(d) == 'Y' and d <= esperado}

    # Cboe manda en todo el historico; Yahoo solo rellena los dias que Cboe no tiene (el ultimo del VIX,
    # que Cboe publica con un dia de retraso, y huecos sueltos). Si Cboe no responde se conserva lo que
    # el CSV anterior ya tenia de Cboe.
    cb = dict(previo.cboe)
    cb.update(cboe or {})
    # Cboe publica algun valor en festivos de EE. UU. (VIX 32 dias desde 2025, VXN/GVZ 2): no son sesiones
    # de cierre, y la regla validada rellena esos dias (fuente R); se ignoran.
    cb = {d: v for d, v in cb.items() if DESDE_CBOE <= d <= esperado and es_dia_de_mercado(d)}
    if not cboe:
        avisos.append(f'{serie.nombre}: Cboe no responde; se conserva lo que el CSV anterior tenia de Cboe')
    for d in sorted(set(cb) & set(y))[-DIAS_CONTRASTE:]:
        if abs(y[d] - cb[d]) > TOLERANCIA_CBOE:
            avisos.append(f'{serie.nombre} {d}: Yahoo {y[d]:.2f} y Cboe {cb[d]:.2f} no coinciden (manda Cboe)')

    tvs = {d: v for d, v in previo.tv.items()}
    if tv is not None and serie.tv:
        if tv.fecha == esperado and esperado not in y and esperado not in cb:
            tvs[esperado] = tv.valor
            avisos.append(f'{serie.nombre}: ni Cboe ni Yahoo tienen {esperado}; se usa TradingView ({tv.valor:.2f})')
        elif tv.fecha == esperado:
            tvs[esperado] = tv.valor                       # solo para contrastar
    if esperado in y and esperado not in cb:
        avisos.append(f'{serie.nombre}: Cboe aun no tiene {esperado}; se usa Yahoo ({y[esperado]:.2f}), '
                      f'provisional hasta que Cboe lo publique')

    reales: dict[date, tuple[float, str]] = {}
    for d, v in y.items():
        if d >= DESDE_CBOE:
            reales[d] = (v, 'Y')
    for d, v in cb.items():
        reales[d] = (v, 'C')
    for d, v in tvs.items():
        if d not in reales and d <= esperado:
            reales[d] = (v, 'T')
    if not reales:
        return Resultado(serie, esperado, [], avisos + [f'{serie.nombre}: sin ningun dato'])

    primera, ultima = min(reales), max(reales)
    fechas = [ultima]
    if serie.calendario:
        hoy = ahora_utc.astimezone(timezone.utc).date()
        d = ultima + timedelta(days=1)
        while d <= hoy and not es_dia_de_mercado(d):       # fin de semana / festivo tras el ultimo cierre
            fechas.append(d)
            d += timedelta(days=1)
        idx = pd.date_range(primera, fechas[-1])
    else:
        idx = pd.DatetimeIndex(sorted(pd.Timestamp(d) for d in reales))

    cierre = pd.Series({pd.Timestamp(d): v for d, (v, _) in reales.items()}).reindex(idx)
    if serie.calendario:
        cierre = cierre.ffill()
    sma = cierre.rolling(SMA).mean()
    filas: list[Fila] = []
    for ts in idx:
        if sma[ts] != sma[ts]:                              # NaN: aun sin 200 dias
            continue
        d = ts.date()
        valor, sm = float(cierre[ts]), float(sma[ts])
        reg = int(valor < sm) if serie.reg_bajo else int(valor > sm)
        fuente = reales[d][1] if d in reales else 'R'
        filas.append(Fila(d, valor, sm, reg, fuente, y.get(d), tvs.get(d)))

    for f in filas[-10:]:
        if f.yahoo is not None and f.tv is not None and abs(f.yahoo - f.tv) / f.yahoo * 100 > TOLERANCIA_PCT:
            avisos.append(f'{serie.nombre} {f.fecha}: Yahoo {f.yahoo:.2f} y TradingView {f.tv:.2f} '
                          f'difieren {abs(f.yahoo - f.tv) / f.yahoo * 100:.2f}%')
        if f.yahoo is not None and f.tv is not None and f.fuente == 'Y':
            otro = int(f.tv < f.sma) if serie.reg_bajo else int(f.tv > f.sma)
            if otro != f.reg:
                avisos.append(f'{serie.nombre} {f.fecha}: el cierre de TradingView daba reg {otro} y el de '
                              f'Yahoo da {f.reg}; si un EA decidio con el primero, revisa su operacion')
    return Resultado(serie, esperado, filas, avisos)


# ---------------------------------------------------------------- escritura y pasada
def escribe_atomico(ruta: str, contenido: str) -> None:
    tmp = ruta + '.nuevo'
    with open(tmp, 'w', encoding='ascii', newline='\n') as f:
        f.write(contenido)
    os.replace(tmp, ruta)


def lee_texto(ruta: str) -> str:
    try:
        with open(ruta, encoding='ascii', errors='replace') as f:
            return f.read()
    except OSError:
        return ''


def pasada(series: list[Serie], out: str, destinos: list[str], ahora_utc: datetime | None = None,
           bajar_yahoo=descarga_yahoo, consultar_tv=consulta_tv, usar_tv: bool = True,
           consultar_cboe=None, usar_cboe: bool = True) -> list[Resultado]:
    """Una pasada: baja Cboe y Yahoo (y TradingView si hace falta), reconstruye y escribe los CSV."""
    consultar_cboe = consultar_cboe or descarga_cboe
    ahora_utc = ahora_utc or datetime.now(timezone.utc)
    os.makedirs(out, exist_ok=True)
    resultados = []
    tv_datos: dict[str, CierreTV] | None = None
    for serie in series:
        previo = lee_previo(lee_texto(os.path.join(out, serie.archivo)))
        try:
            yahoo = bajar_yahoo(serie.yahoo)
        except Exception as e:  # noqa: BLE001
            print(f'ERROR Yahoo {serie.yahoo}: {e}')
            yahoo = None
        cboe = consultar_cboe(serie.nombre) if usar_cboe else None
        tv = None
        esperado = ultimo_cierre_esperado(ahora_utc)
        # TradingView se consulta si Yahoo no tiene el cierre esperado o si aun no hay un dato suyo
        # guardado para contrastar (los primeros dias se comparan los dos).
        falta_yahoo = yahoo is None or not any(i.date() == esperado for i in yahoo.index)
        if usar_tv and serie.tv and (falta_yahoo or esperado not in previo.tv):
            if tv_datos is None:
                tv_datos = consultar_tv([s.tv for s in series if s.tv])
            tv = tv_datos.get(serie.tv)
        r = construye(serie, yahoo, tv, previo, ahora_utc, cboe)
        resultados.append(r)
        if not r.filas:
            print(f'ERROR {serie.nombre}: sin filas; no se escribe nada')
            continue
        for d in [out] + list(destinos):
            try:
                escribe_atomico(os.path.join(d, serie.archivo), r.contenido())
            except OSError as e:
                r.avisos.append(f'ERROR escribiendo en {d}: {e}')
        u = r.ultima_real
        print(f'{serie.nombre} esperado {r.esperado}: {r.estado} - ultima real '
              + (f'{u.fecha} {u.valor:.2f} ({u.fuente})' if u else '-'))
        for a in r.avisos:
            print('  AVISO ' + a)
    return resultados


def escribe_estado(out: str, resultados: list[Resultado], ahora_utc: datetime) -> None:
    estado = {'hora_utc': ahora_utc.strftime('%Y-%m-%dT%H:%M:%SZ'), 'series': {}}
    for r in resultados:
        u = r.ultima_real
        estado['series'][r.serie.nombre] = {
            'esperado': r.esperado.isoformat(), 'estado': r.estado,
            'ultima_real': u.fecha.isoformat() if u else None,
            'fuente': u.fuente if u else None, 'avisos': r.avisos}
    escribe_atomico(os.path.join(out, 'regimen_estado.json'), json.dumps(estado, indent=1))


def _hasta(hhmm: str, ahora: datetime) -> datetime:
    h, m = (int(x) for x in hhmm.split(':'))
    lim = ahora.replace(hour=h, minute=m, second=0, microsecond=0)
    return lim if lim > ahora else lim + timedelta(days=1)


def ejecuta(series: list[Serie], out: str, destinos: list[str], modo: str = 'una-vez', cada_min: int = 60,
            hasta: str = '07:55', ahora_fn=lambda: datetime.now(timezone.utc), dormir=time.sleep,
            reloj_local=datetime.now, **kw) -> int:
    limite = _hasta(hasta, reloj_local())
    while True:
        ahora = ahora_fn()
        res = pasada(series, out, destinos, ahora, **kw)
        escribe_estado(out, res, ahora)
        if any(not r.filas for r in res):
            codigo = 2
        else:
            codigo = 1 if any(r.estado == 'FALTA' for r in res) else 0
        if modo != 'espera' or all(r.estado == 'OK' for r in res):
            return codigo
        if reloj_local() + timedelta(minutes=cada_min) > limite:
            print('Limite horario alcanzado sin confirmar Yahoo en todas las series: '
                  + ', '.join(f'{r.serie.nombre}={r.estado}' for r in res))
            return codigo
        dormir(cada_min * 60)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='Feeds de regimen VXN/VIX/GVZ (Yahoo + TradingView)')
    ap.add_argument('--out', required=True, help='carpeta espejo (tambien guarda el estado)')
    ap.add_argument('--destino', action='append', default=[], help='carpeta MQL5\\Files (repetible)')
    ap.add_argument('--series', default='VXN,VIX,GVZ')
    ap.add_argument('--modo', choices=('una-vez', 'espera'), default='una-vez')
    ap.add_argument('--cada', type=int, default=60, help='minutos entre reintentos (modo espera)')
    ap.add_argument('--hasta', default='07:55', help='hora local limite del modo espera')
    ap.add_argument('--sin-tradingview', action='store_true')
    ap.add_argument('--sin-cboe', action='store_true')
    a = ap.parse_args(argv)
    series = [SERIES[n.strip().upper()] for n in a.series.split(',')]
    return ejecuta(series, a.out, a.destino, a.modo, a.cada, a.hasta, usar_tv=not a.sin_tradingview,
                  usar_cboe=not a.sin_cboe)


if __name__ == '__main__':
    sys.exit(main())
