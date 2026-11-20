# Reconstruir DASH APP desde cero

Este paquete trae el CODIGO y la documentacion. No trae datos, bases, usuarios ni credenciales.

## 1. Entornos Python
- Entorno principal (tablero, build, actualizaciones): Python 3.14 (sirve 3.13). `pip install -r requirements.txt`
- Entorno ML opcional (Cashi ML, monitoreo): Python 3.12. `pip install -r requirements_ml.txt`
- Driver ODBC 17 para SQL Server instalado en Windows (pyodbc lo usa).
- Los .bat apuntan a rutas de entorno fijas: editar la variable `PYTHON_ENV` / ruta de python en `INICIAR_DASH.bat` y `MENU_DASH.bat`.

## 2. Carpetas que hay que crear (vacias)
```
datos/            parquets semanales, catalogos Excel, usuarios.json
aggs/             agregados, _meta.json, dashboard.duckdb, digitales.duckdb
aggs_digitales/   agregados del canal digital
archivos_proyecto/  .env (copiar de .env.ejemplo), logs
salidas/          reportes generados
```

## 3. Entradas que hay que reponer
| Entrada | De donde sale |
|---|---|
| `archivos_proyecto/.env` | Credenciales SQL (ver `.env.ejemplo`) |
| `datos/Catalogo de estructura.xlsx` | Se genera con `codigo/actualizar_estructura.py` desde el Excel oficial de estructura |
| `datos/Catalogo grupo cuentas.xlsx` | Copia manual del Excel oficial de grupos de cuentas |
| `datos/usuarios.json` | Se crea desde la pestana Usuarios o con `gestionar_usuarios` (al menos un admin) |
| `datos/r25_semNN`, `r26_semNN`, `p26_semNN`, `fcst_semNN` `.parquet` | `codigo/extraer_sql.py` desde la base corporativa |

## 4. Orden de arranque
1. `python codigo/extraer_sql.py --tablas r25,r26,p26,fcst`  (descarga y cuadra monto SQL vs parquet)
2. `python codigo/actualizar_estructura.py`  (catalogo de estructura)
3. `python codigo/build_data.py --rebuild --actualizar-catalogos`  (consolidado + aggs, ~22 min)
4. `python codigo/build_data.py --solo duckdb`  (genera `aggs/dashboard.duckdb`, ~7 min)
5. `python codigo/build_digitales_db.py`  (canal digital)
6. `INICIAR_DASH.bat`  (tablero en el puerto 8050)
7. Opcional: `python rag/server_api.py` (chat Cashi web, puerto 8507)

## 5. Actualizacion semanal
`MENU_DASH.bat`, opcion A (descarga Real 2026, consolidado, aggs y base DuckDB, ~25 min, con el tablero apagado). La semana vigente sale de `aggs/_meta.json` (`sem_max_real`, `sem_max_plan`), nunca de un numero escrito a mano.

## 6. Reglas que no se rompen
- El tablero sirve solo con DuckDB: un `cursor()` por consulta y un candado compartido (evita crashes nativos por hilos).
- Builds largos en ventana aparte y con `--solo <etapa>`.
- Antes de declarar que cuadra: `python codigo/pruebas_cuadre.py`.
- Cashi no usa modelo de lenguaje: router por patrones + plantillas SQL + ML local de entidades.
- Leer primero `documentacion/LEEME.md`.
