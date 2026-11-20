"""Escribe UNA serie de _consolidado.parquet en la tabla `consolidado` de un archivo .duckdb (la crea si es la primera).
Lo lanza build_data.build_dashboard_db, una vez por serie y cada vez en un proceso NUEVO: medido el 2026-10-29, escribir
las 86 M filas en un solo proceso tarda 250 a 434 s (la escritura de texto se degrada con el volumen escrito por proceso),
y por serie en procesos nuevos 126 s. Uso: python _escribe_consolidado.py <parquet> <db> <serie> <primera 0|1> <hilos> <memoria> <tmp>"""
import sys
import duckdb

parquet, db, serie, primera, hilos, memoria, tmp = sys.argv[1:8]
ruta = parquet.replace("\\", "/")
con = duckdb.connect(":memory:")
con.execute(f"PRAGMA threads={int(hilos)}")
con.execute(f"PRAGMA memory_limit='{memoria}'")
con.execute("PRAGMA preserve_insertion_order=false")
con.execute(f"PRAGMA temp_directory='{tmp}'")
con.execute(f"ATTACH '{db.replace(chr(92), '/')}' AS o")
cols = {r[0] for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{ruta}')").fetchall()}
excluir = " EXCLUDE (monto)" if "monto" in cols else ""
# mismo criterio que build_data._sql_consolidado_final: un CECO 'No Clasificado' sin movimiento neto se omite
sel = f"""SELECT *{excluir} FROM read_parquet('{ruta}') r
    WHERE r.Serie = '{serie.replace("'", "''")}' AND r.ID_CENTRO_COSTOS NOT IN (
        SELECT ID_CENTRO_COSTOS FROM read_parquet('{ruta}')
        WHERE cat_Direccion_Division = 'No Clasificado' GROUP BY ID_CENTRO_COSTOS HAVING SUM(monto_cons) = 0)"""
con.execute(f"CREATE TABLE o.consolidado AS {sel}" if primera == "1" else f"INSERT INTO o.consolidado {sel}")
con.execute("CHECKPOINT o")
con.close()
