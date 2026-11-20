# -*- coding: utf-8 -*-
"""
actualizar.py — Orquesta la actualización semanal completa.

    SQL Server  --extraer_sql.py-->  r25/r26/p26.parquet
                                          |
                                    build_data.py  ->  aggs/*.parquet
                                          |
                                    estado.json  ->  la app se refresca sola

    Nvo Plan 2026.xlsx (si cambió) -> nvo_plan_2026.parquet
    (2026-08-26: YA NO se integra al dashboard — se sigue convirtiendo a
    Parquet solo para análisis posterior fuera de la app; build_data.py no lo lee)

Escribe su progreso en `estado.json` para que la app lo muestre en vivo.

Uso:
    python actualizar.py            # menú interactivo (catálogos / servidor / aggs)
    python actualizar.py --auto            # automático de-toda-la-vida, sin preguntar (lo usa la app)
    python actualizar.py --auto --force    # automático, siempre (sin importar si hay novedades)
    python actualizar.py --check           # ¿hay novedades? exit 0 = sí, 1 = no (nunca pregunta)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).parent.resolve()   # /codigo — código y config.yaml
ROOT = BASE.parent                       # raíz del proyecto (tras reorganización 2026-08-14)
DATOS = ROOT / "datos"
AGGS = ROOT / "aggs"                     # agregados precomputados + dashboard.duckdb
ARCHIVOS = ROOT / "archivos_proyecto"
DL = Path.home() / "Downloads"
ESTADO = DATOS / "estado.json"
LOG = ARCHIVOS / "actualizacion.log"

NVO_XLSX = DL / "Nvo Plan 2026.xlsx"
NVO_PQ = DATOS / "nvo_plan_2026.parquet"
PY = sys.executable

# La consola de Windows es cp1252: imprimir acentos (o el U+FFFD que mete
# errors="replace") lanza UnicodeEncodeError y aborta la actualización.
# Forzamos UTF-8 en stdout/stderr y no dependemos de la codificación del entorno.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def log(msg: str):
    linea = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    try:
        print(linea, flush=True)
    except UnicodeEncodeError:              # consola sin UTF-8: no perdemos el log
        print(linea.encode("ascii", "replace").decode("ascii"), flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(linea + "\n")


def set_estado(fase: str, pct: int, detalle: str = "", error: str | None = None):
    import os
    ESTADO.write_text(json.dumps({
        "fase": fase, "pct": pct, "detalle": detalle, "error": error,
        "actualizado": datetime.now().isoformat(timespec="seconds"),
        "corriendo": fase not in ("listo", "error", "sin_cambios"),
        "pid": os.getpid(),          # la app lo usa para detectar un proceso muerto
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _entorno_utf8() -> dict:
    """Los hijos también deben hablar UTF-8, o su print() de acentos los mata."""
    import os
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def _dibuja_barra(n: int, total: int, detalle: str, pct: float | None = None) -> None:
    """Barra de progreso en una sola línea (se redibuja con \\r). `pct` (0-100) es el avance ponderado que reporta
    build_data.py; sin él se usa n/total."""
    ancho = 30
    frac = min(1.0, pct / 100 if pct is not None else n / total)
    llena = int(ancho * frac)
    texto = detalle[:80].ljust(80)
    sys.stdout.write(f"\r  [{'#' * llena}{'-' * (ancho - llena)}] {frac * 100:5.1f}%  {n}/{total}  {texto}")
    sys.stdout.flush()


def corre(script: str, *args) -> int:
    """Ejecuta un script del proyecto y transmite su salida al log.

    Las líneas de progreso del build (tqdm `n/total [` o `Paso n/total:`) se
    muestran como UNA barra que se redibuja en la consola, no como cientos de
    líneas. El resto de la salida sigue yendo al log. Así se ve el avance de
    `aggs` y del rebuild completo.
    """
    import re
    patron = re.compile(r"(?:Paso )?(\d+)/(\d+)(?: \[|:)")
    cmd = [PY, str(BASE / script), *args]
    log(f"$ {' '.join(cmd[1:])}")
    p = subprocess.Popen(cmd, cwd=BASE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace", bufsize=1,
                         env=_entorno_utf8())
    barra_activa = False
    ultimo = None
    for linea in p.stdout:
        linea = linea.rstrip()
        if not linea:
            continue
        m = patron.search(linea)
        if m and int(m.group(2)) > 0:
            n, total = int(m.group(1)), int(m.group(2))
            detalle = linea[m.end():].strip(" |[]")
            mp = re.search(r"· (\d+)%", detalle)          # avance ponderado que imprime build_data.py ("Paso n/N: ... · 31% · ~12m rest.")
            _dibuja_barra(n, total, detalle, int(mp.group(1)) if mp else None)
            barra_activa = True
            ultimo = (n, total)
            continue
        if barra_activa:
            print(flush=True)              # cierra la línea de la barra antes del log
            barra_activa = False
        log(f"  | {linea}")
    if barra_activa:
        print(flush=True)
    rc = p.wait()
    if ultimo:
        log(f"  progreso final: {ultimo[0]}/{ultimo[1]} · código de salida {rc}")
    return rc


def dashboard_db_desactualizado() -> bool:
    """True si aggs/dashboard.duckdb falta o quedó más viejo que
    _consolidado.parquet (es decir, hubo un --rebuild después de la última
    generación de la BD)."""
    cons = AGGS / "_consolidado.parquet"
    ddb = AGGS / "dashboard.duckdb"
    if not cons.exists():
        return False          # sin consolidado no hay nada que regenerar
    if not ddb.exists():
        return True
    return cons.stat().st_mtime > ddb.stat().st_mtime


def regenerar_dashboard_db_si_hace_falta() -> int:
    """Regenera aggs/dashboard.duckdb cuando _consolidado.parquet es más nuevo.

    2026-08-26: ANTES este paso era 100% manual — build_data.py solo lo
    MENCIONABA en un texto de ayuda ("Después, corre también: python
    generate_dashboard_db.py"). Si se olvidaba (fácil: es un segundo comando
    después de una corrida de ~20-48 min), la app quedaba en un estado
    inconsistente SILENCIOSO: los parquets ya traían la semana nueva, pero la
    búsqueda de CECO de "Detalle CECO" —que consulta dashboard.duckdb por SQL,
    ver _DDB_PATH en app.py— seguía sirviendo la semana vieja, sin ningún
    aviso al usuario. Ahora se encadena solo al final de la actualización.

    Devuelve el código de salida (0 = ok o no hacía falta)."""
    if not dashboard_db_desactualizado():
        log("dashboard.duckdb: ya está al día (no se regenera).")
        return 0
    log("dashboard.duckdb: _consolidado.parquet es más nuevo — regenerando "
        "(si no, la búsqueda de CECO en 'Detalle CECO' mostraría la semana vieja)...")
    rc = corre("generate_dashboard_db.py")
    if rc != 0:
        log(f"[ERROR] generate_dashboard_db.py falló (código {rc}) — "
            f"la búsqueda de CECO en 'Detalle CECO' puede mostrar datos viejos. "
            f"Corre a mano: python codigo/generate_dashboard_db.py")
    return rc


def nvo_plan_desactualizado() -> bool:
    if not NVO_XLSX.exists():
        return False
    if not NVO_PQ.exists():
        return True
    return NVO_XLSX.stat().st_mtime > NVO_PQ.stat().st_mtime


def regenerar_nvo_plan():
    """Convierte el Excel del Nvo Plan (265 MB, formato ancho) a parquet."""
    import polars as pl
    log(f"Nvo Plan: {NVO_XLSX.name} es más nuevo que el parquet, reconvirtiendo...")
    t0 = time.time()
    for kw in ({"infer_schema_length": 0},
               {"read_csv_options": {"infer_schema_length": 0}},
               {"read_options": {"infer_schema_length": 0}}):
        try:
            df = pl.read_excel(NVO_XLSX, **kw)
            break
        except TypeError:
            continue
    else:
        df = pl.read_excel(NVO_XLSX)
    tmp = NVO_PQ.with_suffix(".parquet.tmp")
    df.write_parquet(tmp)
    tmp.replace(NVO_PQ)
    log(f"Nvo Plan: {df.height:,} filas -> {NVO_PQ.name} ({time.time()-t0:.0f}s)")


def hay_novedades(sem: list[str] = ()) -> bool:
    """True si hay datos nuevos en SQL Server (Real/Plan). Lanza si no se pudo
    consultar SQL (un fallo de red NO debe leerse como 'todo al día').
    2026-08-26: un Nvo Plan 2026.xlsx nuevo YA NO cuenta como novedad — dejó de
    integrarse al dashboard, así que ya no amerita un --rebuild completo por sí
    solo. Su conversión a parquet (regenerar_nvo_plan) sigue corriendo aparte,
    siempre que su Excel cambió, sin depender de esta función."""
    rc = corre("extraer_sql.py", "--check", *sem)
    if rc == 2:
        # el motivo real ya quedó en el log: lo repetimos en el mensaje
        detalle = ""
        try:
            lineas = LOG.read_text(encoding="utf-8").splitlines()[-14:]
            err = [l for l in lineas if "ERROR" in l or "IM003" in l or "driver" in l.lower()]
            if err:
                detalle = " · " + err[-1].split("| ", 1)[-1][:150]
        except OSError as e:
            log(f"AVISO: no se pudo leer {LOG.name} para detallar el error: {e}")
        raise RuntimeError(f"no se pudo consultar SQL Server{detalle}")
    return rc == 0


# =========================================================================
# Menú interactivo: catálogos / servidor / aggs, cada uno con su alcance.
# 2026-08-26 (pedido del usuario): AHORA ES EL DEFAULT de main() — antes
# requería pasar --menu a propósito. La app sigue llamando a este script con
# la bandera --auto explícita para su actualización de fondo (ver
# app.py: `subprocess.Popen([..., "actualizar.py", "--auto"])`), sin
# terminal ni stdin real -> sin --auto ese proceso se quedaría esperando un
# input() que nunca llega y la actualización automática de la app se
# colgaría para siempre. --auto es la ÚNICA forma de saltarse el menú.
# =========================================================================

def _preguntar(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        # sin stdin real (ej. se invocó --menu desde un proceso sin terminal
        # por error) -> mejor cancelar limpio que colgarse.
        print("\n(sin entrada disponible — cancelando)")
        return "0"


def menu_interactivo(sem_args: list[str]):
    """Wizard manual: catálogos / datos del servidor / aggs, cada uno con su
    propio alcance, en vez de la actualización automática de todo-o-nada."""
    from build_data import ETAPAS, CATALOG_ONLY_ETAPAS

    print("=" * 70)
    print("Actualización manual del Dashboard — ¿qué quieres actualizar?")
    print("=" * 70)
    print("  1) Catálogos (Catálogo de estructura/cuentas, responsables, riesgos)")
    print("  2) Info nueva del servidor SQL (Real 2025 / Real 2026 / Plan 2026)")
    print("  3) Aggs (recalcular las tablas del dashboard)")
    print("  4) Todo (equivalente a la actualización automática completa)")
    print("  5) Solo Forecast Finanzas (sin servidor)")
    print("  6) Solo FCST PLAN (sin servidor)")
    print("  7) Solo Real 2026 (servidor SQL + aggs)")
    print("  8) Solo Real 2025 (servidor SQL + aggs)")
    print("  9) Solo Plan 2026 (servidor SQL + aggs)")
    print("  0) Cancelar")
    raw = _preguntar("\nElige una o varias, separadas por coma (ej. 2,3) >>> ")
    if not raw or raw == "0":
        print("Cancelado.")
        return

    elegidas = {t.strip() for t in raw.split(",") if t.strip()}
    todo = "4" in elegidas
    quiere_catalogos = todo or "1" in elegidas
    quiere_server = todo or "2" in elegidas
    quiere_aggs = todo or "3" in elegidas
    # 2026-11-10: 5 = solo forecast_cuenta, 6 = solo fcst_srv (sin servidor); se suman a lo demás elegido.
    quiere_fcst_fin = "5" in elegidas and not todo
    quiere_fcst_srv = "6" in elegidas and not todo
    # 7/8/9 = una serie del servidor con su propio --tablas; fijan el alcance y no preguntan tablas ni etapas.
    mapa_srv_solo = {"7": "r26", "8": "r25", "9": "p26"}
    tablas_solas = [] if todo else [mapa_srv_solo[k] for k in ("7", "8", "9") if k in elegidas]
    quiere_server_sola = bool(tablas_solas)
    if quiere_server_sola:
        quiere_server = True
        quiere_aggs = True

    if not (quiere_catalogos or quiere_server or quiere_aggs or quiere_fcst_fin or quiere_fcst_srv):
        print("Nada válido elegido. Cancelado.")
        return

    tablas_server_arg = None  # None = las 3 (default de extraer_sql.py)
    if quiere_server_sola:
        tablas_server_arg = ",".join(tablas_solas)
    elif quiere_server and not todo:
        print("\n¿Cuáles tablas del servidor? (Enter = las 3)")
        print("  1) Real 2025   2) Real 2026   3) Plan 2026")
        raw_t = _preguntar(">>> ")
        if raw_t:
            mapa = {"1": "r25", "2": "r26", "3": "p26"}
            elegidas_t = [mapa[t.strip()] for t in raw_t.split(",") if t.strip() in mapa]
            if elegidas_t:
                tablas_server_arg = ",".join(elegidas_t)

    # Traer datos nuevos del servidor sin luego recalcular los aggs deja la
    # app viendo la semana vieja (los aggs son lo único que la app lee) ->
    # si se pide "servidor" se implica "aggs" también, salvo que el usuario
    # ya haya elegido "todo" (que ya cubre ambos).
    if quiere_server and not todo:
        quiere_aggs = True

    etapas_arg = "semanal" if quiere_server_sola else None  # None = preset "semanal" (default sensato)
    if quiere_aggs and not todo and not quiere_server_sola:
        print("\n¿Qué aggs? Enter = 'semanal' (todo lo que depende de datos, sin catálogo).")
        print("Nombres de etapa (dims, ceco, cierres, pdc_pospre...) o de dimensión")
        print("(pdc, division, cuentas...) separados por coma, o 'todo' para el rebuild completo.")
        print(f"Etapas disponibles: {', '.join(sorted(ETAPAS))}")
        raw_a = _preguntar(">>> ")
        etapas_arg = raw_a if raw_a else "semanal"

    # Etapas de build_data.py a pedir con --solo (irrelevante si `todo`,
    # que hace un --rebuild sin --solo). Se calcula UNA sola vez y se usa
    # tanto para el resumen como para la ejecución real, más abajo.
    piezas = set()
    if not todo:
        if quiere_fcst_fin:
            piezas.add("forecast_cuenta")
        if quiere_fcst_srv:
            piezas.add("fcst_srv")
        if quiere_catalogos:
            piezas |= set(CATALOG_ONLY_ETAPAS)
        if quiere_aggs and etapas_arg not in (None, "todo"):
            if etapas_arg == "semanal":
                piezas.add("semanal")
            else:
                piezas |= {t.strip() for t in etapas_arg.split(",") if t.strip()}

    # Real 2026 se actualiza cada jueves, pero traer datos nuevos no siempre
    # debe significar "avanzar la semana que muestra el tablero" (a veces se
    # quiere revisar antes). Si esta corrida toca Real 2026, se pregunta
    # explícito en vez de dejar que detect_cortes() decida solo.
    incluye_r26 = quiere_server and (not tablas_server_arg or "r26" in tablas_server_arg.split(","))
    forzar_semana_real = None
    if incluye_r26:
        semana_actual = None
        try:
            semana_actual = json.loads((AGGS / "_meta.json").read_text(encoding="utf-8-sig")).get("sem_max_real")
        except (OSError, json.JSONDecodeError, ValueError):
            pass
        if semana_actual is not None:
            print(f"\nEl tablero muestra Real 2026 hasta la semana {semana_actual} ahora mismo.")
            resp = _preguntar(
                f"Con los datos nuevos, ¿avanzar el tablero a la semana que cierre "
                f"[Enter = avanzar] o mantenerlo en la semana {semana_actual} "
                f"[m = mantener] >>> "
            ).strip().casefold()
            if resp.startswith("m"):
                forzar_semana_real = semana_actual
                print(f"  -> se guardan los datos nuevos, pero el tablero se queda en la semana {semana_actual}.")
            else:
                print("  -> el tablero avanzará a la semana que detecte build_data.py como cerrada.")

    # ---- Resumen y confirmación ----
    print("\nResumen de lo que se va a correr:")
    if quiere_catalogos and not todo:
        print(f"  - Catálogos: {', '.join(sorted(CATALOG_ONLY_ETAPAS))}")
    if quiere_server:
        print(f"  - Servidor SQL: {tablas_server_arg or 'las 3 tablas (Real 2025/2026, Plan 2026)'}")
    if forzar_semana_real is not None:
        print(f"  - Semana del tablero: se mantiene forzada en {forzar_semana_real}")
    if todo:
        print("  - Aggs: TODO (--rebuild completo, ~48 min)")
    elif piezas:
        con_rebuild = " (con --rebuild, hay datos nuevos del servidor)" if quiere_server else ""
        print(f"  - Aggs: --solo {','.join(sorted(piezas))}{con_rebuild}")
    confirmar = _preguntar("\n¿Confirmar? [s/N] >>> ").casefold()
    if confirmar != "s":
        print("Cancelado.")
        return

    t0 = time.time()

    if quiere_server:
        server_args = list(sem_args)
        if tablas_server_arg:
            server_args += ["--tablas", tablas_server_arg]
        log("Extrayendo del servidor SQL...")
        rc = corre("extraer_sql.py", *server_args)
        if rc not in (0, 1):
            log(f"[ERROR] extraer_sql.py falló (código {rc})")
            sys.exit(1)

    if quiere_catalogos:
        log("Reconvirtiendo Catálogos Excel a Parquet optimizados...")
        rc = corre("catalogos.py", "--force")
        if rc != 0:
            log(f"[ERROR] catalogos.py falló (código {rc})")
            sys.exit(1)

    if nvo_plan_desactualizado():
        log("Nvo Plan: el Excel cambió, reconvirtiendo...")
        regenerar_nvo_plan()

    forzar_args = ["--forzar-semana-real", str(forzar_semana_real)] if forzar_semana_real is not None else []
    if todo:
        log("Corriendo build_data.py --rebuild (todo)...")
        rc = corre("build_data.py", "--rebuild", *sem_args, *forzar_args)
    elif piezas:
        build_args = (["--rebuild"] if quiere_server else []) + ["--solo", ",".join(sorted(piezas))] + sem_args + forzar_args
        log(f"Corriendo build_data.py {' '.join(build_args)}...")
        rc = corre("build_data.py", *build_args)
    else:
        rc = 0

    if rc != 0:
        log(f"[ERROR] build_data.py falló (código {rc})")
        sys.exit(1)

    # dashboard.duckdb depende de _consolidado.parquet: si el build lo
    # reconstruyó, hay que regenerar la BD o "Detalle CECO" queda con la
    # semana vieja (ver regenerar_dashboard_db_si_hace_falta).
    regenerar_dashboard_db_si_hace_falta()

    mins = (time.time() - t0) / 60
    log(f"[OK] actualización manual completa en {mins:.1f} min")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--auto", action="store_true",
                    help="se salta el menú interactivo — actualización automática de "
                         "todo-o-nada (comportamiento de antes de 2026-08-26). Usado por "
                         "la app para su actualización de fondo; nunca a mano si esperas "
                         "poder contestar algo.")
    ap.add_argument("--actualizar-catalogos", action="store_true",
                    help="(solo con --auto) fuerza la reconversión de Catálogos Excel a Parquet")
    ap.add_argument("--menu", action="store_true",
                    help="(compatibilidad — ya es el default sin --auto) menú interactivo")
    ap.add_argument("--semana", type=int, default=None,
                    help="semana ARG a extraer (por defecto, la vigente)")
    args = ap.parse_args()
    sem = ["--semana", str(args.semana)] if args.semana is not None else []

    if args.check:
        try:
            sys.exit(0 if hay_novedades(sem) else 1)
        except RuntimeError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(2)   # 0=hay novedades · 1=sin cambios · 2=no se pudo saber

    if not args.auto:
        menu_interactivo(sem)
        return

    t0 = time.time()
    try:
        set_estado("comprobando", 2, "buscando datos nuevos en SQL Server")
        if not args.force and not hay_novedades(sem):
            log("Sin cambios. Nada que actualizar.")
            set_estado("sin_cambios", 100, "los datos ya estaban al día")
            return

        set_estado("extrayendo", 10, "trayendo Real 2025 / Real 2026 / Plan 2026 de SQL Server (~10 min)")
        rc = corre("extraer_sql.py", *(["--force"] if args.force else []), *sem)
        if rc != 0:
            # 0x40010004 = DBG_TERMINATE_PROCESS: alguien lo mató (Ctrl-C, cerrar
            # ventana). La extracción tarda ~10 min; no está colgada.
            if rc == 1073807364:
                raise RuntimeError(
                    "la extracción se canceló desde fuera (Ctrl-C o ventana cerrada). "
                    "Tarda ~10 min: déjala correr, ahora imprime avance por lotes.")
            raise RuntimeError(f"extraer_sql.py falló (código {rc})")

        if args.force or args.actualizar_catalogos:
            set_estado("catalogos", 20, "reconvirtiendo Catálogos Excel a Parquet")
            corre("catalogos.py", "--force")

        if args.force or nvo_plan_desactualizado():
            set_estado("nvo_plan", 35, "reconvirtiendo Nvo Plan 2026.xlsx")
            regenerar_nvo_plan()

        # Si extraer_sql.py NO encontró datos nuevos pero hay datos de la semana anterior,
        # usa --resume para continuar desde caché (consolida solo los aggs sin reconstruir
        # las 95M de filas). Esto ahorra ~8 min cuando los datos no tienen cambios.
        # Los datos Real/Plan solo se actualizan 1x/mes con cambio de UDI.
        # 2026-08-19: antes usaba nombres "sem32" hardcodeados -> una vez pasada
        # esa semana, los archivos correctos nunca existen bajo ese nombre y
        # has_new_data quedaba siempre True, forzando --rebuild en cada corrida.
        sys.path.insert(0, str(BASE))
        from semana import nombres as _nombres_semana
        _sem_actual = args.semana if args.semana is not None else None
        has_new_data = args.force or any([
            not Path(DATOS / f).exists() for f in _nombres_semana(_sem_actual).values()
        ])
        build_args = ["--rebuild"] if has_new_data else ["--resume"]

        set_estado("agregando", 45, "build_data.py — une 95 M filas y precomputa (~8 min)")
        rc = corre("build_data.py", *build_args, *sem)
        if rc != 0:
            raise RuntimeError(f"build_data.py falló (código {rc})")

        set_estado("indexando", 90, "generate_dashboard_db.py — BD de búsqueda de CECO")
        regenerar_dashboard_db_si_hace_falta()

        mins = (time.time() - t0) / 60
        log(f"[OK] actualización completa en {mins:.1f} min")
        set_estado("listo", 100, f"terminado en {mins:.1f} min")

    except Exception as e:
        log(f"[ERROR] {e}")
        log(traceback.format_exc())
        set_estado("error", 0, "la actualización falló", error=str(e)[:300])
        sys.exit(1)


if __name__ == "__main__":
    main()
