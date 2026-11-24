import os
import json
import math
import time
import requests
from datetime import datetime, timezone, timedelta, date
from zoneinfo import ZoneInfo

VERSION = "1.0.1"

# ==============================================================================
# APEX QUANT SNOOKER
# Misma familia que Apex Quant fútbol, basket, tenis y hockey. Como en hockey, SIN
# cuotas de casas: el bot calcula con su modelo la probabilidad de cada mercado y la
# CUOTA JUSTA (1 / probabilidad). Tú la comparas con Winamax: si Winamax paga al
# menos un 5 % más que la cuota justa (la «cuota mínima»), hay valor.
#
# Fuente gratuita: snooker.org (api.snooker.org). Gratis para uso no comercial,
# citando la fuente. Para descargas automáticas piden una cabecera X-Requested-By
# acordada con ellos (secreto SNOOKER_ORG_HEADER). Ver README.
#
# Modelo:
#   1) Elo POR FRAMES de cada jugador con los partidos de esta temporada y la
#      anterior (cada frame ganado o perdido mueve el Elo).
#   2) Probabilidad de ganar cada frame -> carrera a N frames (al mejor de 2N-1)
#      calculada de forma exacta -> ganador, hándicap, total de frames y marcador.
# Lo que el modelo NO sabe: cansancio, lesiones, motivación ni el estado del día.
# ==============================================================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
APP_URL = os.getenv("APP_URL", "https://apexquantbot.github.io/apex-quant-snooker/")
SNOOKER_ORG_HEADER = os.getenv("SNOOKER_ORG_HEADER", "").strip()

TZ_MADRID = ZoneInfo("Europe/Madrid")
API = "https://api.snooker.org/"

# Margen que se exige sobre la cuota justa para que haya valor (1.05 = +5 %)
MARGEN_VALOR = float(os.getenv("MARGEN_VALOR", "1.05"))
# Pronóstico del modelo para cada partido: probabilidad mínima/máxima y cuota mínima
PICK_PROB_MIN = float(os.getenv("PICK_PROB_MIN", "0.60"))
PICK_PROB_MAX = float(os.getenv("PICK_PROB_MAX", "0.85"))
PICK_CUOTA_MIN = float(os.getenv("PICK_CUOTA_MIN", "1.30"))
PICKS_MAX_DIA = int(os.getenv("PICKS_MAX_DIA", "6"))        # tope de destacados por día
PICK_HORAS_ANTES = float(os.getenv("PICK_HORAS_ANTES", "18"))  # el pronóstico se fija como mucho 18 h antes

# Parámetros del modelo
ELO_INICIAL = float(os.getenv("ELO_INICIAL", "1450"))
K_FRAME = float(os.getenv("K_FRAME", "4"))          # lo que se mueve el Elo por cada frame
MIN_FRAMES = int(os.getenv("MIN_FRAMES", "30"))     # frames mínimos de cada jugador para dar cuotas
AJUSTE_FRAME = float(os.getenv("AJUSTE_FRAME", "0.95"))  # prudencia: acerca un poco al 50 % la prob. de cada frame

# snooker.org pide no abusar: como mucho ~10 peticiones por minuto
PAUSA_API = float(os.getenv("PAUSA_API", "6.5"))
MAX_PETICIONES = int(os.getenv("MAX_PETICIONES", "60"))      # por ciclo
RELLENO_MAX_EVENTOS = int(os.getenv("RELLENO_MAX_EVENTOS", "25"))
DIAS_VISTA = 3                     # partidos que se muestran: hasta 3 días por delante
ID_TBD = {0, 376}                  # «por decidir» en snooker.org

INICIO_CICLO = time.time()
DIAG = {"api_ok": 0, "api_error": 0, "peticiones": 0, "ultimo_error": None, "permiso": None, "cabecera": None,
        "muestras": []}


def tiempo_usado():
    return time.time() - INICIO_CICLO


# ==============================================================================
# SNOOKER.ORG
# ==============================================================================
_ultima_peticion = [0.0]


def _cabeceras(modo):
    h = {"User-Agent": "ApexQuantSnooker/1.0 (+https://apexquantbot.github.io/apex-quant-snooker/)",
         "Accept": "application/json"}
    if modo == "secreto" and SNOOKER_ORG_HEADER:
        h["X-Requested-By"] = SNOOKER_ORG_HEADER
    elif modo == "generica":
        h["X-Requested-By"] = "ApexQuantSnooker"
    return h


def api(params, est):
    """Una petición a snooker.org respetando la pausa. Devuelve la lista/objeto JSON o None."""
    if DIAG["peticiones"] >= MAX_PETICIONES:
        return None
    if DIAG["permiso"] is False and not DIAG["api_ok"] and DIAG["api_error"] >= 4:
        return None      # snooker.org no nos deja entrar: no se insiste en este ciclo
    # Sin la cabecera acordada y ya rechazados: solo se vuelve a probar cada 6 h (por cortesía con snooker.org)
    neg = _fecha_utc(est.get("denegado"))
    if not SNOOKER_ORG_HEADER and neg and (datetime.now(timezone.utc) - neg).total_seconds() < 6 * 3600:
        DIAG["permiso"] = False
        DIAG["ultimo_error"] = {"q": params, "st": "en pausa: esperando la cabecera de snooker.org", "desde": est.get("denegado")}
        return None
    modos = [est.get("cabecera_ok")] if est.get("cabecera_ok") else (["secreto"] if SNOOKER_ORG_HEADER else ["ninguna", "generica"])
    for modo in modos:
        espera = PAUSA_API - (time.time() - _ultima_peticion[0])
        if espera > 0:
            time.sleep(espera)
        _ultima_peticion[0] = time.time()
        DIAG["peticiones"] += 1
        st = None
        try:
            res = requests.get(API, params=params, headers=_cabeceras(modo), timeout=(6, 25))
            st = res.status_code
            if st == 200:
                try:
                    data = res.json()
                except Exception:
                    data = None
                    st = "200-no-json"
                if data is not None and not (isinstance(data, dict) and ("error" in {k.lower() for k in data})):
                    DIAG["api_ok"] += 1
                    DIAG["permiso"] = True
                    est.pop("denegado", None)
                    DIAG["cabecera"] = modo
                    est["cabecera_ok"] = modo
                    if len(DIAG["muestras"]) < 6:
                        DIAG["muestras"].append({"q": params, "st": 200, "n": len(data) if isinstance(data, list) else 1})
                    return data
                if st == 200:
                    st = "200-error"
        except Exception as e:
            st = f"red: {str(e)[:80]}"
        DIAG["api_error"] += 1
        DIAG["ultimo_error"] = {"q": params, "st": st, "cabecera": modo}
        if st in (401, 403, "200-error", "200-no-json") and DIAG["permiso"] is not True:
            DIAG["permiso"] = False
            est["denegado"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        print(f"   ⚠️ snooker.org {params}: {st} (cabecera: {modo})")
        if est.get("cabecera_ok") and st in (401, 403):
            est.pop("cabecera_ok", None)   # la que valía ya no vale: la próxima vez se prueban todas
    return None


def _i(v, defecto=0):
    try:
        if v is None or str(v).strip() == "":
            return defecto
        return int(float(v))
    except Exception:
        return defecto


def _g(d, *claves, defecto=None):
    """Lee un campo admitiendo variaciones de mayúsculas (Player1ID / player1id...)."""
    if not isinstance(d, dict):
        return defecto
    for c in claves:
        if c in d and d[c] not in (None, ""):
            return d[c]
    bajas = {k.lower(): v for k, v in d.items()}
    for c in claves:
        v = bajas.get(c.lower())
        if v not in (None, ""):
            return v
    return defecto


def _fecha_utc(s):
    if not s:
        return None
    try:
        t = datetime.fromisoformat(str(s).strip().replace("Z", "+00:00").replace(" ", "T"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return t
    except Exception:
        return None


def _fecha_dia(s):
    try:
        return date.fromisoformat(str(s)[:10])
    except Exception:
        return None


def temporada_actual(hoy):
    """En snooker.org la temporada 2026 es la 2026/27 (de junio a mayo)."""
    return hoy.year if hoy.month >= 6 else hoy.year - 1


PALABRAS_FUERA = ("shoot out", "shootout", "shoot-out", "6 red", "6-red", "six red", "six-red", "world cup", "nations",
                  "team", "doubles", "women", "ladies", "senior", "legends", "u21", "u-21", "under-", "under 21",
                  "junior", "q school", "q tour", "challenge tour", "pro-am", "pro am", "exhibition", "amateur",
                  "heyball", "pool", "billiards", "snooker 900")


def evento_valido(ev):
    nombre = str(_g(ev, "Name", defecto="")).lower()
    if any(p in nombre for p in PALABRAS_FUERA):
        return False
    disc = str(_g(ev, "Discipline", defecto="snooker")).lower()
    if disc and "snooker" not in disc:
        return False
    if _g(ev, "Team") in (True, 1, "1", "true"):
        return False
    if str(_g(ev, "Sex", defecto="")).upper() in ("F", "W"):
        return False
    if str(_g(ev, "AgeGroup", defecto="")).upper() in ("S", "U", "J", "Y"):
        return False
    if str(_g(ev, "Type", defecto="")).lower() in ("exhibition",):
        return False
    return True


def cargar_temporada(db, temporada, hoy):
    """Eventos de una temporada (t=5). La actual se refresca cada 12 h; las pasadas, una vez."""
    clave = str(temporada)
    t = db["temporadas"].get(clave)
    caduca = 12 if temporada == temporada_actual(hoy) else 24 * 365
    if t and t.get("t"):
        hace = (datetime.now(timezone.utc) - _fecha_utc(t["t"])).total_seconds() / 3600
        if hace < caduca:
            return t["eventos"]
    data = api({"t": 5, "s": temporada}, db["estado"])
    if not isinstance(data, list):
        return (t or {}).get("eventos", [])
    eventos = []
    for ev in data:
        eid = _i(_g(ev, "ID"))
        if not eid:
            continue
        eventos.append({"id": eid, "n": str(_g(ev, "Name", defecto=f"Evento {eid}")).strip(),
                        "ini": str(_g(ev, "StartDate", defecto=""))[:10], "fin": str(_g(ev, "EndDate", defecto=""))[:10],
                        "tipo": str(_g(ev, "Type", defecto="")), "ciudad": str(_g(ev, "City", defecto="")),
                        "pais": str(_g(ev, "Country", defecto="")), "ok": evento_valido(ev)})
    db["temporadas"][clave] = {"t": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "eventos": eventos}
    print(f"📅 Temporada {temporada}/{(temporada + 1) % 100:02d}: {len(eventos)} eventos ({sum(1 for e in eventos if e['ok'])} válidos)")
    return eventos


# ------------------------------------------------------------------------------
# Rondas: cuántos frames hay que ganar en cada ronda (t=12) y su nombre
# ------------------------------------------------------------------------------
NOMBRES_RONDA = [("semi-final", "Semifinal"), ("semi final", "Semifinal"), ("quarter-final", "Cuartos"),
                 ("quarter final", "Cuartos"), ("final", "Final"), ("last 16", "Octavos"), ("last 32", "Dieciseisavos"),
                 ("last 48", "Últimos 48"), ("last 64", "Ronda de 64"), ("last 80", "Últimos 80"),
                 ("last 96", "Últimos 96"), ("last 112", "Últimos 112"), ("last 128", "Ronda de 128"),
                 ("round robin", "Liguilla"), ("group", "Grupo"), ("qualifying", "Previa"), ("round", "Ronda"),
                 ("wildcard", "Wildcard"), ("play-off", "Play-off"), ("playoff", "Play-off")]


def nombre_ronda(txt, r):
    t = (txt or "").strip()
    tl = t.lower()
    for en, es in NOMBRES_RONDA:
        if tl.startswith(en):
            return es + t[len(en):] if es in ("Ronda", "Grupo") else es
    return t or f"Ronda {r}"


def cargar_rondas(db, eid, forzar=False):
    info = db["rondas"].get(str(eid))
    if info and not forzar:
        return info
    data = api({"t": 12, "e": eid}, db["estado"])
    info = dict(info or {})
    if isinstance(data, list):
        for x in data:
            r = _i(_g(x, "Round"))
            if not r:
                continue
            dist = _i(_g(x, "Distance"))
            info[str(r)] = {"d": dist if 1 <= dist <= 25 else 0, "n": nombre_ronda(_g(x, "RoundName", defecto=""), r)}
    info["_t"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    db["rondas"][str(eid)] = info
    return info


# ------------------------------------------------------------------------------
# Jugadores (t=9 por evento; ?p=ID si falta alguno)
# ------------------------------------------------------------------------------
def _jugador_compacto(x):
    pid = _i(_g(x, "ID"))
    nombre = str(_g(x, "FirstName", defecto="")).strip()
    medio = str(_g(x, "MiddleName", defecto="")).strip()
    apellido = str(_g(x, "LastName", defecto="")).strip()
    sur = _g(x, "SurnameFirst") in (True, 1, "1", "true")
    if sur:
        completo = f"{apellido} {nombre}".strip()
        corto = completo
    else:
        completo = " ".join(v for v in (nombre, medio, apellido) if v)
        corto = apellido or completo
    if not completo:
        completo = str(_g(x, "ShortName", defecto=f"Jugador {pid}"))
        corto = completo
    return pid, {"n": completo, "c": corto, "nac": str(_g(x, "Nationality", defecto=""))}


def cargar_jugadores_evento(db, eid):
    marca = db["jug_t"].get(str(eid))
    if marca and (datetime.now(timezone.utc) - _fecha_utc(marca)).total_seconds() < 6 * 3600:
        return
    data = api({"t": 9, "e": eid}, db["estado"])
    if isinstance(data, list):
        for x in data:
            pid, j = _jugador_compacto(x)
            if pid:
                db["jugadores"][str(pid)] = j
        db["jug_t"][str(eid)] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def cargar_jugador(db, pid):
    data = api({"p": pid}, db["estado"])
    x = data[0] if isinstance(data, list) and data else data
    if isinstance(x, dict):
        p, j = _jugador_compacto(x)
        if p:
            db["jugadores"][str(p)] = j


# ------------------------------------------------------------------------------
# Partidos de un evento (t=6)
# ------------------------------------------------------------------------------
def parsear_partido(x, eid):
    r, n = _i(_g(x, "Round")), _i(_g(x, "Number"))
    p1, p2 = _i(_g(x, "Player1ID")), _i(_g(x, "Player2ID"))
    s1, s2 = _i(_g(x, "Score1")), _i(_g(x, "Score2"))
    w = _i(_g(x, "WinnerID"))
    wo = _g(x, "Walkover1") in (True, 1, "1", "true") or _g(x, "Walkover2") in (True, 1, "1", "true")
    status = _i(_g(x, "Status"), -1)
    if w > 0 and w in (p1, p2):
        estado = "FINISHED"
    elif status in (1, 2) or _g(x, "Unfinished") in (True, 1, "1", "true") and (s1 + s2) > 0:
        estado = "IN_PLAY"
    elif status == 3:
        estado = "PENDING_RESULT"
    else:
        estado = "TIMED"
    cuando = _g(x, "ScheduledDate") or _g(x, "StartDate") or ""
    return {"id": f"{eid}-{r}-{n}", "eid": eid, "ronda": r, "num": n, "p1": p1, "p2": p2, "s1": s1, "s2": s2,
            "ganador": (1 if w == p1 else 2) if estado == "FINISHED" else None, "walkover": wo,
            "estado": estado, "descanso": status == 2, "fecha": str(cuando),
            "fin": str(_g(x, "EndDate", defecto="")), "estimada": _g(x, "Estimated") in (True, 1, "1", "true"),
            "mesa": _i(_g(x, "TableNo"))}


def descargar_evento(db, eid):
    data = api({"t": 6, "e": eid}, db["estado"])
    if not isinstance(data, list):
        return None
    return [parsear_partido(x, eid) for x in data if isinstance(x, dict)]


def distancia(db, m):
    """Frames que hay que ganar (N). Lo más fiable: lo que ya se vio en partidos terminados de la ronda."""
    info = db["rondas"].get(str(m["eid"])) or {}
    ronda = info.get(str(m["ronda"])) or {}
    visto = db["dist_vista"].get(f"{m['eid']}-{m['ronda']}")
    return visto or ronda.get("d") or 0


def apuntar_distancias(db, partidos):
    for m in partidos:
        if m["estado"] == "FINISHED" and not m["walkover"]:
            k = f"{m['eid']}-{m['ronda']}"
            db["dist_vista"][k] = max(db["dist_vista"].get(k, 0), max(m["s1"], m["s2"]))


def registrar_resultado(db, m, fecha_evento):
    """Guarda un partido terminado para el Elo: [fecha, p1, p2, s1, s2]."""
    if m["estado"] != "FINISHED" or m["walkover"] or m["p1"] in ID_TBD or m["p2"] in ID_TBD:
        return False
    if max(m["s1"], m["s2"]) < 2:      # partidos a un frame (shoot-out, desempates): no sirven
        return False
    if m["id"] in db["partidos"]:
        return False
    f = _fecha_utc(m.get("fin")) or _fecha_utc(m.get("fecha"))
    fecha = f.astimezone(TZ_MADRID).date().isoformat() if f else (fecha_evento or "")
    db["partidos"][m["id"]] = [fecha, m["p1"], m["p2"], m["s1"], m["s2"]]
    return True


# ==============================================================================
# BASE DE DATOS LOCAL (snooker.json)
# ==============================================================================
ARCHIVO_DB = "snooker.json"


def cargar_db():
    d = {}
    if os.path.exists(ARCHIVO_DB):
        try:
            with open(ARCHIVO_DB, "r", encoding="utf-8") as f:
                d = json.load(f)
        except Exception as e:
            print(f"Aviso leyendo {ARCHIVO_DB}: {e}")
    for k in ("temporadas", "eventos_hechos", "partidos", "jugadores", "rondas", "jug_t", "dist_vista", "estado"):
        d.setdefault(k, {})
    d["version"] = 1
    return d


def guardar_db(db, hoy):
    limite = (hoy - timedelta(days=760)).isoformat()
    db["partidos"] = {k: v for k, v in db["partidos"].items() if (v[0] or "9") >= limite}
    with open(ARCHIVO_DB, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, separators=(",", ":"))


# ==============================================================================
# ELO POR FRAMES
# ==============================================================================
def calcular_elo(db):
    elo, frames = {}, {}
    for v in sorted(db["partidos"].values(), key=lambda x: x[0] or ""):
        _, a, b, sa, sb = v
        ra, rb = elo.get(a, ELO_INICIAL), elo.get(b, ELO_INICIAL)
        e = 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))
        dif = sa - e * (sa + sb)
        ka = K_FRAME * (2.5 if frames.get(a, 0) < 150 else (1.5 if frames.get(a, 0) < 400 else 1.0))
        kb = K_FRAME * (2.5 if frames.get(b, 0) < 150 else (1.5 if frames.get(b, 0) < 400 else 1.0))
        elo[a] = ra + ka * dif
        elo[b] = rb - kb * dif
        frames[a] = frames.get(a, 0) + sa + sb
        frames[b] = frames.get(b, 0) + sa + sb
    return {"elo": elo, "frames": frames}


def racha(db, pid, n=5):
    juegos = sorted((v for v in db["partidos"].values() if pid in (v[1], v[2])), key=lambda v: v[0] or "")[-n:]
    return "".join("W" if (v[3] > v[4]) == (v[1] == pid) else "L" for v in juegos)


# ==============================================================================
# MODELO DEL PARTIDO: carrera a N frames
# ==============================================================================
def distribucion(p, N):
    """Probabilidad de cada marcador final (a, b) en una carrera a N frames con prob. p por frame."""
    q = 1 - p
    dist = {}
    for k in range(N):
        c = math.comb(N - 1 + k, k)
        dist[(N, k)] = c * p ** N * q ** k
        dist[(k, N)] = c * q ** N * p ** k
    return dist


def _num(x):
    return ("%g" % x)


def construir_mercados(dist, N, A, B):
    """Devuelve {clave: (grupo, tipo, texto, prob)} con los mercados que tienen sentido para ese N."""
    M = {}
    pa = sum(v for (a, b), v in dist.items() if a > b)
    M["ML1"] = ("ganador", "GANADOR", f"Gana {A}", pa)
    M["ML2"] = ("ganador", "GANADOR", f"Gana {B}", 1 - pa)
    for h in (1.5, 2.5, 3.5):
        if h >= N:
            continue
        pa_h = sum(v for (a, b), v in dist.items() if a - b > h)
        pb_h = sum(v for (a, b), v in dist.items() if b - a > h)
        M[f"H1-{_num(h)}"] = ("handicap", "HANDICAP", f"Hándicap {A} -{_num(h)} frames", pa_h)
        M[f"H2+{_num(h)}"] = ("handicap", "HANDICAP", f"Hándicap {B} +{_num(h)} frames", 1 - pa_h)
        M[f"H2-{_num(h)}"] = ("handicap", "HANDICAP", f"Hándicap {B} -{_num(h)} frames", pb_h)
        M[f"H1+{_num(h)}"] = ("handicap", "HANDICAP", f"Hándicap {A} +{_num(h)} frames", 1 - pb_h)
    lineas = []
    for t in range(N, 2 * N - 1):
        linea = t + 0.5
        po = sum(v for (a, b), v in dist.items() if a + b > linea)
        if 0.12 <= po <= 0.88:
            lineas.append((abs(po - 0.5), linea, po))
    for _, linea, po in sorted(sorted(lineas)[:4], key=lambda x: x[1]):
        M[f"O{_num(linea)}"] = ("frames", "FRAMES", f"Más de {_num(linea)} frames", po)
        M[f"U{_num(linea)}"] = ("frames", "FRAMES", f"Menos de {_num(linea)} frames", 1 - po)
    for (a, b), v in sorted(dist.items(), key=lambda x: -x[1])[:6]:
        M[f"E{a}-{b}"] = ("exacto", "EXACTO", f"Resultado exacto {A} {a}-{b} {B}", v)
    return M


def calcular_modelo(m, N, fuerza, A, B):
    if not fuerza or N < 2:
        return None
    f1, f2 = fuerza["frames"].get(m["p1"], 0), fuerza["frames"].get(m["p2"], 0)
    if f1 < MIN_FRAMES or f2 < MIN_FRAMES:
        return None
    r1, r2 = fuerza["elo"].get(m["p1"], ELO_INICIAL), fuerza["elo"].get(m["p2"], ELO_INICIAL)
    p = 1.0 / (1.0 + 10 ** ((r2 - r1) / 400.0))
    p = 0.5 + (p - 0.5) * AJUSTE_FRAME
    dist = distribucion(p, N)
    mercados = construir_mercados(dist, N, A, B)
    probs = {k: min(0.995, max(0.005, v[3])) for k, v in mercados.items()}
    esperado = sum((a + b) * v for (a, b), v in dist.items())
    return {"p_frame": round(p, 3), "elo1": round(r1), "elo2": round(r2), "frames1": f1, "frames2": f2,
            "N": N, "frames_esperados": round(esperado, 1), "probs": probs,
            "mercados": {k: (v[0], v[1], v[2]) for k, v in mercados.items()}}


def cuota_justa(p):
    return round(1.0 / p, 2) if p and p > 0 else None


def cuota_minima(p):
    return round(MARGEN_VALOR / p, 2) if p and p > 0 else None


def tabla_cuotas(modelo):
    filas = []
    for clave, (grupo, tipo, texto) in modelo["mercados"].items():
        p = modelo["probs"][clave]
        filas.append({"k": clave, "g": grupo, "t": texto, "p": round(p * 100, 1), "j": cuota_justa(p), "m": cuota_minima(p)})
    return filas


def elegir_pick(tabla):
    """Pronóstico del modelo: entre los mercados con probabilidad 60-85 % y cuota mínima >= 1,30,
    el de cuota más alta. El resultado exacto nunca entra (demasiado improbable)."""
    validos = []
    for f in tabla:
        if f["g"] == "exacto":
            continue
        p = f["p"] / 100
        if not (PICK_PROB_MIN <= p <= PICK_PROB_MAX) or (f["m"] or 0) < PICK_CUOTA_MIN:
            continue
        validos.append((f["m"], f["k"], p, f))
    if not validos:
        return None
    cmin, clave, p, f = max(validos)
    tipo = {"ganador": "GANADOR", "handicap": "HANDICAP", "frames": "FRAMES"}[f["g"]]
    return {"clave": clave, "mercado": f["t"], "tipo_mercado": tipo, "categoria_visual": f["g"], "prob": p,
            "prob_modelo": f"{round(p * 100, 1)}%", "cuota_justa": f["j"], "cuota": cmin,
            "ev": round((MARGEN_VALOR - 1) * 100, 1), "stake": 1}


def resolver(clave, info):
    """True / False / 'PUSH' / None (aún no se sabe)."""
    if info.get("estado") in ("CANCELLED",) or info.get("walkover"):
        return "PUSH"
    if info.get("estado") != "FINISHED" or not info.get("ganador"):
        return None
    s1, s2, N = info.get("s1", 0), info.get("s2", 0), info.get("N") or 0
    if clave == "ML1":
        return info["ganador"] == 1
    if clave == "ML2":
        return info["ganador"] == 2
    if N and max(s1, s2) < N:
        return "PUSH"      # partido abandonado/concedido antes de tiempo: solo cuenta el ganador
    try:
        if clave[0] == "H":
            lado, h = int(clave[1]), float(clave[2:])
            dif = (s1 - s2) if lado == 1 else (s2 - s1)
            return dif + h > 0
        if clave[0] == "O":
            return s1 + s2 > float(clave[1:])
        if clave[0] == "U":
            return s1 + s2 < float(clave[1:])
        if clave[0] == "E":
            a, b = (int(x) for x in clave[1:].split("-"))
            return s1 == a and s2 == b
    except Exception:
        return None
    return None


# ==============================================================================
# TELEGRAM (opcional: sin secretos no manda nada)
# ==============================================================================
AVISO_JUEGO = "🔞 <i>+18 • Juega con responsabilidad • Información estadística, no es asesoramiento • jugarbien.es</i>"


def enviar_telegram(mensaje, url_accion=None, texto_boton="📱 Abrir en Web App"):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": f"{mensaje}\n\n{AVISO_JUEGO}", "parse_mode": "HTML",
               "disable_web_page_preview": True}
    destino = url_accion or APP_URL
    if destino:
        payload["reply_markup"] = {"inline_keyboard": [[{"text": texto_boton, "url": destino}]]}
    for intento in range(3):
        try:
            res = requests.post(url, json=payload, timeout=12)
            if res.status_code == 200:
                return True
            print(f"⚠️ Telegram rechazó el mensaje (HTTP {res.status_code}): {res.text[:200]}")
            if res.status_code in (400, 401, 403, 404):
                return False
            time.sleep(1.5)
        except Exception as e:
            print(f"Error Telegram intento {intento + 1}: {e}")
            time.sleep(2)
    return False


def _c(p):
    return f"@{cuota_justa(p):.2f}"


def bloque_partido(p):
    pr = p["probs_previa"]
    A, B = p["local_corto"], p["visitante_corto"]
    lineas = [f"🎱 <b>{A} - {B}</b> · {p['hora']} · {p['evento']} ({p['ronda_nombre']}, al mejor de {p['al_mejor_de']})",
              f"🏆 Ganador: {A} {_c(pr['ML1'])} · {B} {_c(pr['ML2'])}"]
    hs = [k for k in pr if k.startswith("H1-")]
    if hs:
        k = hs[0]
        h = k[3:]
        lineas.append(f"⚖️ Hándicap ±{h}: {A} -{h} {_c(pr[k])} · {B} +{h} {_c(pr['H2+' + h])}")
    if p.get("telegram_enviado") and p.get("mercado"):
        lineas.append(f"📌 <b>Pronóstico:</b> {p['mercado']} · {p['prob_modelo']} · <b>cuota mínima @{p['cuota']:.2f}</b>")
    return "\n".join(lineas)


# ==============================================================================
# SEMANAS, HISTÓRICOS Y RESÚMENES (igual que fútbol, basket, tenis y hockey)
# ==============================================================================
def cargar_json(nombre, defecto):
    if os.path.exists(nombre):
        try:
            with open(nombre, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Aviso leyendo {nombre}: {e}")
    return defecto


def guardar_json(nombre, datos, compacto=False):
    try:
        with open(nombre, "w", encoding="utf-8") as f:
            if compacto:
                json.dump(datos, f, ensure_ascii=False, separators=(",", ":"))
            else:
                json.dump(datos, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Aviso guardando {nombre}: {e}")


def obtener_identificador_semana(fecha_madrid):
    anio, semana, _ = fecha_madrid.isocalendar()
    return f"{anio}-W{semana:02d}"


def obtener_fechas_limite_semana(fecha_madrid):
    lunes_dt = fecha_madrid - timedelta(days=fecha_madrid.weekday())
    return lunes_dt.strftime("%Y-%m-%d"), (lunes_dt + timedelta(days=6)).strftime("%Y-%m-%d")


def archivar_historico_mensual(alertas_viejas, mes_id):
    historico = cargar_json("historico_mensual.json", {})
    historico.setdefault(mes_id, [])
    ids = {a.get("id_alerta") for a in historico[mes_id]}
    for a in alertas_viejas:
        if a.get("id_alerta") not in ids:
            historico[mes_id].append(a)
    guardar_json("historico_mensual.json", historico)


def _a_float(txt, defecto):
    try:
        return float(str(txt).split("/")[0].replace("@", "").replace("U", "").replace("+", "").replace("%", "").strip())
    except Exception:
        return defecto


def calcular_resumen_financiero(alertas_lista):
    apostadas, neto, acertadas, finalizadas = 0.0, 0.0, 0, 0
    for a in alertas_lista:
        if a.get("finalizado") and not a.get("push"):
            finalizadas += 1
            stk = _a_float(a.get("stake", "1"), 1.0)
            cuota = _a_float(a.get("cuota", "@2.00"), 2.0)
            apostadas += stk
            if a.get("acertado") is True:
                acertadas += 1
                neto += stk * (cuota - 1.0)
            else:
                neto -= stk
    roi = round((neto / apostadas) * 100, 1) if apostadas > 0 else 0.0
    return {"finalizadas": finalizadas, "acertadas": acertadas, "falladas": finalizadas - acertadas,
            "unidades_apostadas": round(apostadas, 1), "unidades_netas": round(neto, 2), "roi_pct": roi}


def formatear_fecha_corta(fecha_str):
    p = (fecha_str or "").split("-")
    return f"{p[2]}/{p[1]}" if len(p) == 3 else (fecha_str or "")


ARCHIVO_LEDGER = "historico_picks.json"
ARCHIVO_CALIBRACION = "calibracion.json"


def actualizar_ledger(alertas, partidos):
    ledger = cargar_json(ARCHIVO_LEDGER, {"version": 1, "picks": []})
    picks = ledger.get("picks", [])
    ids = {p.get("id") for p in picks}
    nuevas = 0
    for a in alertas:
        if not (a.get("finalizado") and a.get("acertado") is not None and not a.get("push")):
            continue
        pid = f"TG_{a.get('id_alerta')}"
        if pid in ids:
            continue
        picks.append({"id": pid, "version": VERSION, "grupo": "TG", "fecha": a.get("fecha"), "mes": (a.get("fecha") or "")[:7],
                      "semana_id": a.get("semana_id"), "partido": a.get("partido"), "mercado": a.get("mercado"),
                      "casa": None, "cuota": _a_float(a.get("cuota"), 2.0), "stake": _a_float(a.get("stake"), 1.0),
                      "ev": _a_float(a.get("ev"), 0.0), "confirmado_stats": True, "acertado": bool(a.get("acertado")),
                      "solo_modelo": True, "tipo": a.get("tipo_mercado"), "marcador": a.get("marcador_final"),
                      "evento": a.get("liga")})
        ids.add(pid)
        nuevas += 1
    for p in partidos:
        if not (p.get("estado") == "FINISHED" and p.get("acertado") is not None and p.get("tiene_pronostico")
                and not p.get("telegram_enviado") and not p.get("push")):
            continue
        pid = f"CTRL_{p.get('id')}_{p.get('clave_pick')}"
        if pid in ids:
            continue
        picks.append({"id": pid, "version": VERSION, "grupo": "CONTROL", "fecha": p.get("fecha"), "mes": (p.get("fecha") or "")[:7],
                      "semana_id": p.get("semana_id"), "partido": f"{p.get('local')} vs {p.get('visitante')}",
                      "mercado": p.get("mercado"), "casa": None, "cuota": _a_float(p.get("cuota"), 2.0),
                      "stake": _a_float(p.get("stake"), 1.0), "ev": _a_float(p.get("ev_numero"), 0.0),
                      "confirmado_stats": True, "acertado": bool(p.get("acertado")), "solo_modelo": True,
                      "tipo": p.get("tipo_mercado"), "marcador": p.get("marcador"), "evento": p.get("evento")})
        ids.add(pid)
        nuevas += 1
    if nuevas:
        ledger["picks"] = picks
        guardar_json(ARCHIVO_LEDGER, ledger, compacto=True)
        print(f"📒 Libro mayor: +{nuevas} picks (total {len(picks)})")


def actualizar_calibracion(partidos):
    """De cada partido terminado guarda la probabilidad que dio el modelo a cada mercado y si salió."""
    cal = cargar_json(ARCHIVO_CALIBRACION, {"version": 1, "partidos": []})
    ids = {x.get("id") for x in cal.get("partidos", [])}
    nuevas = 0
    for p in partidos:
        if p.get("estado") != "FINISHED" or not p.get("probs_previa") or p.get("id") in ids:
            continue
        info = {"estado": "FINISHED", "ganador": p.get("ganador"), "s1": p.get("goles_local") or 0,
                "s2": p.get("goles_visitante") or 0, "N": p.get("frames_ganar"), "walkover": p.get("walkover")}
        sal = {}
        for k in p["probs_previa"]:
            r = resolver(k, info)
            if isinstance(r, bool):
                sal[k] = 1 if r else 0
        if not sal:
            continue
        cal["partidos"].append({"id": p["id"], "f": p.get("fecha"), "p": p["probs_previa"], "o": sal})
        ids.add(p["id"])
        nuevas += 1
    if nuevas:
        guardar_json(ARCHIVO_CALIBRACION, cal, compacto=True)
        print(f"🎯 Calibración: +{nuevas} partidos (total {len(cal['partidos'])})")


# ==============================================================================
# CICLO PRINCIPAL
# ==============================================================================
def ejecutar_ciclo():
    ahora_utc = datetime.now(timezone.utc)
    ahora_madrid = ahora_utc.astimezone(TZ_MADRID)
    hora_madrid_str = ahora_madrid.strftime("%H:%M")
    hoy = ahora_madrid.date()
    semana_actual_id = obtener_identificador_semana(ahora_madrid)
    mes_actual_id = ahora_madrid.strftime("%Y-%m")
    dia_semana, hora_int = ahora_madrid.weekday(), ahora_madrid.hour
    lunes_semana_str, domingo_semana_str = obtener_fechas_limite_semana(ahora_madrid)
    lunes = date.fromisoformat(lunes_semana_str)
    print(f"[{hora_madrid_str}] Iniciando ciclo Apex Quant Snooker v{VERSION} (Semana {semana_actual_id})...")

    datos_previos = cargar_json("datos.json", {})
    alertas = datos_previos.get("alertas", [])
    partidos_previos = datos_previos.get("partidos", [])
    semana_guardada = datos_previos.get("semana_activa", semana_actual_id)
    resumen_semanal_enviado = datos_previos.get("resumen_semanal_enviado_para", "")
    resumen_mensual_enviado = datos_previos.get("resumen_mensual_enviado_para", "")

    telegram_ok = bool(datos_previos.get("telegram_ok"))
    if not telegram_ok and TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        telegram_ok = enviar_telegram(
            "🎱 <b>APEX QUANT SNOOKER</b> ya está en marcha.\n\n"
            "Recibirás los pronósticos del modelo con su <b>cuota mínima</b> y la <b>cuota justa</b> de cada mercado "
            "(ganador, hándicap de frames, total de frames) para compararla con Winamax. Al terminar, los resultados.",
            None, "📱 Abrir Apex Quant Snooker")

    if semana_actual_id != semana_guardada:
        print(f"🔄 Nueva semana ({semana_guardada} -> {semana_actual_id})")
        archivar_historico_mensual([a for a in alertas if not a.get("es_semana_actual", True)], mes_actual_id)
        for a in alertas:
            a["es_semana_actual"] = False
        semana_guardada = semana_actual_id

    db = cargar_db()
    est = db["estado"]
    temp = temporada_actual(hoy)
    eventos = cargar_temporada(db, temp, hoy)
    eventos_prev = cargar_temporada(db, temp - 1, hoy)
    todos_eventos = {e["id"]: e for e in eventos_prev + eventos}

    # 1) Eventos en juego (o que empiezan en los próximos días): se descargan en cada ciclo
    activos = []
    for e in eventos:
        ini, fin = _fecha_dia(e["ini"]), _fecha_dia(e["fin"])
        if not e["ok"] or not ini or not fin:
            continue
        if ini - timedelta(days=1) <= hoy + timedelta(days=DIAS_VISTA) and fin + timedelta(days=1) >= lunes - timedelta(days=1):
            activos.append(e)
    activos.sort(key=lambda e: e["ini"])
    partidos_vivos = []
    for e in activos:
        lote = descargar_evento(db, e["id"])
        if lote is None:
            continue
        apuntar_distancias(db, lote)
        for m in lote:
            registrar_resultado(db, m, e["fin"])
        cargar_jugadores_evento(db, e["id"])
        info = db["rondas"].get(str(e["id"]))
        caduca = not info or (datetime.now(timezone.utc) - _fecha_utc(info.get("_t"))).total_seconds() > 12 * 3600
        if caduca:
            cargar_rondas(db, e["id"], forzar=True)
        for m in lote:
            m["evento"] = e
        partidos_vivos.extend(lote)
    print(f"🎱 Eventos en juego: {', '.join(e['n'] for e in activos) or 'ninguno'} · {len(partidos_vivos)} partidos")

    # 2) Relleno del historial (temporada anterior y lo ya jugado de esta) para el Elo
    rellenados = 0
    pendientes = [e for e in sorted(eventos_prev + eventos, key=lambda x: x["ini"])
                  if e["ok"] and str(e["id"]) not in db["eventos_hechos"] and _fecha_dia(e["fin"])
                  and _fecha_dia(e["fin"]) < hoy - timedelta(days=1)]
    for e in pendientes:
        if rellenados >= RELLENO_MAX_EVENTOS or DIAG["peticiones"] >= MAX_PETICIONES - 8 or tiempo_usado() > 600:
            break
        lote = descargar_evento(db, e["id"])
        if lote is None:
            if DIAG["permiso"] is False:
                break
            continue
        apuntar_distancias(db, lote)
        nuevos = sum(1 for m in lote if registrar_resultado(db, m, e["fin"]))
        db["eventos_hechos"][str(e["id"])] = nuevos
        rellenados += 1
    if rellenados:
        faltan = len(pendientes) - rellenados
        print(f"📚 Historial: +{rellenados} eventos descargados" + (f" (faltan {faltan}, siguen en próximos ciclos)" if faltan > 0 else " (al día)"))

    # Nombres que falten de los partidos que se van a mostrar
    faltan_j = {pid for m in partidos_vivos for pid in (m["p1"], m["p2"]) if pid not in ID_TBD and str(pid) not in db["jugadores"]}
    for pid in list(faltan_j)[:8]:
        cargar_jugador(db, pid)

    fuerza = calcular_elo(db) if len(db["partidos"]) >= 200 else None
    historial_pendiente = len([e for e in pendientes if str(e["id"]) not in db["eventos_hechos"]])
    if fuerza and historial_pendiente > 15:
        fuerza = None      # mientras se descarga la temporada anterior, el Elo aún no es fiable
    print(f"📚 {len(db['partidos'])} partidos en el historial · {len(db['jugadores'])} jugadores · "
          f"{'modelo listo' if fuerza else 'el modelo aún no tiene datos suficientes'}")

    # 3) Partidos para la web
    previos_map = {str(x.get("id")): x for x in partidos_previos}
    partidos_web, mapa_info = [], {}
    limite_vista = hoy + timedelta(days=DIAS_VISTA)

    def jugador(pid):
        j = db["jugadores"].get(str(pid)) or {}
        return j.get("n") or f"Jugador {pid}", j.get("c") or j.get("n") or f"Jugador {pid}", j.get("nac") or ""

    for m in partidos_vivos:
        if m["p1"] in ID_TBD or m["p2"] in ID_TBD:
            continue
        utc = _fecha_utc(m.get("fecha"))
        if not utc:
            continue
        mad = utc.astimezone(TZ_MADRID)
        fecha_p, hora_p = mad.date().isoformat(), mad.strftime("%H:%M")
        if fecha_p < lunes_semana_str and m["estado"] == "FINISHED":
            continue
        if mad.date() > limite_vista:
            continue
        e = m["evento"]
        N = distancia(db, m)
        A, Acorto, Anac = jugador(m["p1"])
        B, Bcorto, Bnac = jugador(m["p2"])
        minutos = round((utc - ahora_utc).total_seconds() / 60.0)
        estado = m["estado"]
        if estado == "TIMED" and minutos < -24 * 60:
            estado = "PENDING_RESULT"
        if m["walkover"] and estado != "IN_PLAY":
            estado = "CANCELLED"
        marcador = f"{m['s1']}-{m['s2']}" if (estado in ("IN_PLAY", "FINISHED") or m["s1"] + m["s2"] > 0) else None
        info = {"estado": estado, "ganador": m["ganador"], "s1": m["s1"], "s2": m["s2"], "N": N,
                "walkover": m["walkover"], "marcador": marcador}
        mapa_info[m["id"]] = info
        ronda_info = (db["rondas"].get(str(e["id"])) or {}).get(str(m["ronda"])) or {}

        prev = previos_map.get(m["id"]) or {}
        mismo_cruce = prev.get("p1") == m["p1"] and prev.get("p2") == m["p2"]
        if not mismo_cruce:
            prev = {}
        modelo, tabla, probs = prev.get("modelo"), prev.get("cuotas_justas"), prev.get("probs_previa")
        if estado == "TIMED" and not prev.get("analizado"):
            try:
                mod = calcular_modelo(m, N, fuerza, Acorto, Bcorto)
                if mod:
                    tabla = tabla_cuotas(mod)
                    probs = {k: round(v, 4) for k, v in mod["probs"].items()}
                    modelo = {k: v for k, v in mod.items() if k not in ("probs", "mercados")}
                else:
                    tabla, probs, modelo = None, None, None
            except Exception as ex:
                print(f"   Aviso modelo {A} - {B}: {ex}")

        item = {
            "id": m["id"], "p1": m["p1"], "p2": m["p2"], "local": A, "visitante": B,
            "local_corto": Acorto, "visitante_corto": Bcorto, "nac_local": Anac, "nac_visitante": Bnac,
            "evento": e["n"], "evento_id": e["id"], "evento_lugar": ", ".join(x for x in (e.get("ciudad"), e.get("pais")) if x),
            "liga": e["n"], "liga_key": "snooker", "ronda": m["ronda"],
            "ronda_nombre": ronda_info.get("n") or f"Ronda {m['ronda']}",
            "frames_ganar": N or None, "al_mejor_de": (2 * N - 1) if N else None,
            "fecha": fecha_p, "jornada": fecha_p, "hora": hora_p,
            "fecha_hora_display": f"{formatear_fecha_corta(fecha_p)} • {hora_p}" + (" (aprox.)" if m.get("estimada") else ""),
            "utcDate": utc.strftime("%Y-%m-%dT%H:%M:%SZ"), "timestamp_ms": int(utc.timestamp() * 1000),
            "minutos_restantes": minutos, "estado": estado, "descanso": m.get("descanso"),
            "marcador": marcador, "goles_local": m["s1"] if marcador else None, "goles_visitante": m["s2"] if marcador else None,
            "ganador": m["ganador"], "walkover": m["walkover"], "mesa": m.get("mesa") or None,
            "modelo": modelo, "cuotas_justas": tabla, "probs_previa": probs,
            "form_info": {"racha_local": racha(db, m["p1"]), "racha_visitante": racha(db, m["p2"])},
            "semana_id": semana_actual_id if fecha_p >= lunes_semana_str else obtener_identificador_semana(mad),
            "analizado": bool(prev.get("analizado")), "tiene_pronostico": bool(prev.get("tiene_pronostico")),
            "telegram_enviado": bool(prev.get("telegram_enviado")), "acertado": None, "push": False,
        }
        for k in ("mercado", "tipo_mercado", "categoria_visual", "clave_pick", "cuota", "cuota_justa", "prob_modelo",
                  "ev", "ev_numero", "stake", "nota_cuotas"):
            item[k] = prev.get(k)
        if item["tiene_pronostico"] and item["clave_pick"]:
            res = resolver(item["clave_pick"], info)
            if res == "PUSH":
                item["push"] = True
            elif isinstance(res, bool):
                item["acertado"] = res
        partidos_web.append(item)

    # Un partido terminado no «retrocede» por un dato atrasado
    for i, it in enumerate(partidos_web):
        pv = previos_map.get(str(it.get("id")))
        if pv and pv.get("estado") == "FINISHED" and it.get("estado") != "FINISHED" and pv.get("p1") == it.get("p1"):
            r = dict(pv)
            r["minutos_restantes"] = it.get("minutos_restantes")
            partidos_web[i] = r
            mapa_info[r["id"]] = {"estado": "FINISHED", "ganador": r.get("ganador"), "s1": r.get("goles_local") or 0,
                                  "s2": r.get("goles_visitante") or 0, "N": r.get("frames_ganar"),
                                  "walkover": r.get("walkover"), "marcador": r.get("marcador")}

    # 4) PRONÓSTICOS: cada partido se fija como mucho 18 h antes (en snooker el rival se conoce tarde)
    nuevos = []
    for p in partidos_web:
        if p["analizado"] or p["estado"] != "TIMED":
            continue
        mins = p.get("minutos_restantes", 0)
        if mins > PICK_HORAS_ANTES * 60:
            continue
        if mins <= 5:
            p["analizado"] = True
            p["nota_cuotas"] = p.get("nota_cuotas") or "El cruce se conoció demasiado tarde para el pronóstico"
            continue
        if not p.get("frames_ganar"):
            continue          # se espera a saber a cuántos frames es
        if not fuerza:
            continue          # se espera a que el modelo esté listo
        p["analizado"] = True
        if not p.get("cuotas_justas"):
            p["nota_cuotas"] = "Sin datos suficientes de algún jugador para el modelo (pocos frames jugados)"
            continue
        pk = elegir_pick(p["cuotas_justas"])
        if not pk:
            p["nota_cuotas"] = "El modelo no ve un pronóstico claro (ningún mercado entre 60 % y 85 %)"
            continue
        p.update({"tiene_pronostico": True, "mercado": pk["mercado"], "tipo_mercado": pk["tipo_mercado"],
                  "categoria_visual": pk["categoria_visual"], "clave_pick": pk["clave"], "cuota": pk["cuota"],
                  "cuota_justa": pk["cuota_justa"], "prob_modelo": pk["prob_modelo"], "ev": f"+{pk['ev']}%",
                  "ev_numero": pk["ev"], "stake": pk["stake"],
                  "nota_cuotas": f"Pronóstico del modelo: {pk['prob_modelo']} · cuota justa @{pk['cuota_justa']:.2f} · mínima @{pk['cuota']:.2f}"})
        nuevos.append((pk["prob"], p))

    # Destacados: los más seguros según el modelo, hasta 6 por día; el resto queda como Control
    destacados_nuevos = []
    for _, p in sorted(nuevos, key=lambda x: -x[0]):
        ya = sum(1 for a in alertas if a.get("fecha") == p["fecha"])
        if ya >= PICKS_MAX_DIA:
            continue
        p["telegram_enviado"] = True
        alertas.append({
            "id_partido": p["id"], "id_alerta": f"{p['id']}_{p['p1']}_{p['p2']}_{p['clave_pick']}",
            "partido": f"{p['local']} vs {p['visitante']}", "local": p["local"], "visitante": p["visitante"],
            "nac_local": p["nac_local"], "nac_visitante": p["nac_visitante"],
            "mercado": p["mercado"], "tipo_mercado": p["tipo_mercado"], "categoria_visual": p["categoria_visual"],
            "clave_pick": p["clave_pick"], "cuota_justa": p["cuota_justa"], "cuota_minima": True, "solo_modelo": True,
            "cuotas_reales": False, "prob_modelo": p["prob_modelo"], "liga": p["evento"], "liga_code": "SNK",
            "ronda": p["ronda_nombre"], "confirmado_stats": True, "cuota": f"@{p['cuota']:.2f}", "ev": p["ev"],
            "stake": f"{p['stake']}/10", "hora": p["hora"], "fecha": p["fecha"], "fecha_hora_display": p["fecha_hora_display"],
            "semana_id": p["semana_id"], "es_semana_actual": True, "estado_partido": "TIMED", "marcador": None,
            "marcador_final": None, "finalizado": False, "acertado": None, "liquidada_telegram": False})
        destacados_nuevos.append(p)
    if nuevos:
        print(f"📌 {len(nuevos)} pronósticos nuevos ({len(destacados_nuevos)} destacados)")
    if destacados_nuevos and TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        cab = (f"🎱 <b>APEX QUANT SNOOKER — nuevos pronósticos</b>\nCuotas justas del modelo (horas de España). "
               f"<b>Hay valor si Winamax paga al menos un {round((MARGEN_VALOR - 1) * 100)} % más</b> que la justa.")
        enviar_telegram(cab + "\n\n" + "\n\n".join(bloque_partido(p) for p in sorted(destacados_nuevos, key=lambda x: x["timestamp_ms"])),
                        url_accion=APP_URL, texto_boton="📱 Ver todas las cuotas justas")

    # 5) LIQUIDACIÓN
    for a in alertas:
        if a.get("finalizado"):
            continue
        info = mapa_info.get(str(a.get("id_partido", "")))
        if not info:
            continue
        a["estado_partido"] = info["estado"]
        if info.get("marcador"):
            a["marcador"] = info["marcador"]
        res = resolver(a.get("clave_pick"), info)
        if res is None:
            continue
        es_push = res == "PUSH"
        a.update({"finalizado": True, "marcador_final": info.get("marcador") or ("Anulado" if es_push else None),
                  "acertado": None if es_push else bool(res), "push": es_push})
        if not a.get("liquidada_telegram"):
            cab = f"🎱 <b>{a['partido']}</b>\n📌 <b>Pronóstico:</b> {a['mercado']}\n"
            if es_push:
                cuerpo = "↩️ <b>Resultado:</b> ANULADA (walkover o partido no completado)\n\n⚡ <i>Apex Quant Engine</i>"
            elif res:
                cuerpo = (f"✅ <b>Resultado:</b> ACERTADO\n🏁 <b>Marcador final:</b> {a['marcador_final'] or 'Final'}\n"
                          f"💰 <b>Cuota mínima:</b> {a['cuota']} (justa @{float(a.get('cuota_justa') or 0):.2f})\n💡 <b>Stake:</b> {a['stake']}\n\n⚡ <i>Apex Quant Engine</i>")
            else:
                cuerpo = (f"❌ <b>Resultado:</b> PERDIDO\n🏁 <b>Marcador final:</b> {a['marcador_final'] or 'Final'}\n"
                          f"💡 <b>Stake:</b> {a['stake']} (control estricto de banca)\n\n⚡ <i>Apex Quant Engine • Disciplina a largo plazo</i>")
            enviar_telegram(cab + cuerpo, url_accion=APP_URL, texto_boton="📱 Ver en la Web App")
            a["liquidada_telegram"] = True

    # 6) RESÚMENES (solo si hay Telegram)
    semana_pasada_id = obtener_identificador_semana(ahora_madrid - timedelta(days=1))
    if dia_semana == 0 and 9 <= hora_int < 14 and resumen_semanal_enviado != semana_pasada_id:
        sem = [a for a in alertas if a.get("semana_id") == semana_pasada_id and a.get("finalizado")]
        r = calcular_resumen_financiero(sem)
        if r["finalizadas"] > 0:
            pct = round(r["acertadas"] / r["finalizadas"] * 100, 1)
            s = "+" if r["unidades_netas"] >= 0 else ""
            enviar_telegram(
                f"📊 <b>CIERRE SEMANAL — APEX QUANT SNOOKER</b> 🏆\n\n📅 <b>Semana:</b> {semana_pasada_id}\n"
                f"📈 <b>Pronósticos:</b> <b>{r['finalizadas']}</b> · ✅ {r['acertadas']} ({pct}%) · ❌ {r['falladas']}\n"
                f"💰 <b>A cuota mínima:</b> <b>{s}{r['unidades_netas']} U.</b> · ROI {s}{r['roi_pct']}%\n\n⚡ <i>Apex Quant Engine</i>",
                url_accion=APP_URL, texto_boton="📱 Abrir balance completo en la App")
        resumen_semanal_enviado = semana_pasada_id
    mes_pasado = (ahora_madrid.replace(day=1) - timedelta(days=1))
    mes_pasado_id = mes_pasado.strftime("%Y-%m")
    if ahora_madrid.day == 1 and 9 <= hora_int < 14 and resumen_mensual_enviado != mes_pasado_id:
        todas = list(cargar_json("historico_mensual.json", {}).get(mes_pasado_id, []))
        vistos = {a.get("id_alerta") for a in todas}
        todas += [a for a in alertas if (a.get("fecha") or "").startswith(mes_pasado_id) and a.get("finalizado") and a.get("id_alerta") not in vistos]
        r = calcular_resumen_financiero(todas)
        if r["finalizadas"] > 0:
            pct = round(r["acertadas"] / r["finalizadas"] * 100, 1)
            s = "+" if r["unidades_netas"] >= 0 else ""
            enviar_telegram(
                f"🏆 <b>CIERRE MENSUAL — APEX QUANT SNOOKER</b>\n\n📅 <b>Mes:</b> {mes_pasado_id}\n"
                f"📈 <b>Pronósticos:</b> {r['finalizadas']} · ✅ {r['acertadas']} ({pct}%)\n"
                f"💰 <b>A cuota mínima:</b> {s}{r['unidades_netas']} U. · ROI {s}{r['roi_pct']}%\n\n⚡ <i>Apex Quant Engine</i>",
                url_accion=APP_URL, texto_boton="📱 Abrir balance completo en la App")
        resumen_mensual_enviado = mes_pasado_id

    # 7) GUARDADO: partidos de esta semana y de los próximos días
    guardar = [p for p in partidos_web if p.get("fecha", "") >= lunes_semana_str or p.get("estado") in ("IN_PLAY", "TIMED")]
    ids = {p["id"] for p in guardar}
    for pp in partidos_previos:
        if pp.get("id") not in ids and pp.get("fecha", "") >= lunes_semana_str and pp.get("estado") == "FINISHED":
            guardar.append(pp)
            ids.add(pp.get("id"))
    actualizar_ledger(alertas, guardar)
    actualizar_calibracion(guardar)
    guardar_db(db, hoy)

    if DIAG["permiso"] is False and not DIAG["api_ok"]:
        fuente = "sin_permiso"
    elif DIAG["api_error"] and not DIAG["api_ok"]:
        fuente = "error"
    else:
        fuente = "ok"
    top = []
    if fuerza:
        activos_ids = {pid for m in partidos_vivos for pid in (m["p1"], m["p2"])}
        for pid, r in sorted(fuerza["elo"].items(), key=lambda x: -x[1]):
            if fuerza["frames"].get(pid, 0) >= 150 and str(pid) in db["jugadores"]:
                top.append({"id": pid, "n": db["jugadores"][str(pid)]["n"], "nac": db["jugadores"][str(pid)].get("nac", ""),
                            "elo": round(r), "juega": pid in activos_ids})
            if len(top) >= 20:
                break
    datos = {
        "version_bot": VERSION, "ultima_actualizacion": hora_madrid_str,
        "ultima_actualizacion_utc": ahora_utc.strftime("%Y-%m-%dT%H:%M:%SZ"), "huso_horario": "Europe/Madrid",
        "semana_activa": semana_guardada, "lunes_semana": lunes_semana_str, "domingo_semana": domingo_semana_str,
        "resumen_semanal_enviado_para": resumen_semanal_enviado, "resumen_mensual_enviado_para": resumen_mensual_enviado,
        "margen_valor": MARGEN_VALOR, "pick_horas_antes": PICK_HORAS_ANTES, "fuente_estado": fuente,
        "eventos_activos": [{"id": e["id"], "n": e["n"], "ini": e["ini"], "fin": e["fin"],
                             "lugar": ", ".join(x for x in (e.get("ciudad"), e.get("pais")) if x)} for e in activos],
        "ranking_elo": top,
        "metricas_semana": calcular_resumen_financiero([a for a in alertas if a.get("semana_id") == semana_actual_id]),
        "total_partidos_hoy": len(guardar), "partidos": guardar, "api_cuotas_estado": "modelo",
        "telegram_ok": telegram_ok,
        "diagnostico": {"fuente": DIAG, "historial_partidos": len(db["partidos"]), "jugadores": len(db["jugadores"]),
                        "eventos_pendientes": historial_pendiente, "modelo_listo": bool(fuerza),
                        "temporada": temp},
        "alertas": [a for a in alertas if a.get("es_semana_actual") or not a.get("finalizado")
                    or (a.get("fecha") or "") >= (lunes - timedelta(days=7)).isoformat()][-400:],
    }
    guardar_json("datos.json", datos, compacto=True)
    print(f"📁 datos.json guardado. Partidos: {len(guardar)} | Alertas: {len(alertas)} | Fuente: {fuente} | "
          f"Peticiones: {DIAG['peticiones']}")


if __name__ == "__main__":
    ejecutar_ciclo()
