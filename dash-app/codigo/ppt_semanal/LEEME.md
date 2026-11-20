# ppt_semanal — Presentación semanal de Canales Físicos (automática)

Genera `Gasto_semana_NN.pptx` (69 láminas: mismo diseño, logos, colores, tablas e hipervínculos que la
presentación aprobada de la semana 39) con las cifras del tablero. Corre sola al terminar una actualización.

## Uso

```
cd codigo
python generar_ppt_semanal.py                 # semana vigente (aggs/_meta.json -> sem_max_real)
python generar_ppt_semanal.py --semana 39     # una semana concreta
python -m ppt_semanal.prueba_oro              # regresión (correr tras cualquier cambio)
python -m ppt_semanal.verificar_layout <pptx> # compara alturas de tabla con PowerPoint
```

Salida en `salidas/presentacion/`: `Gasto_semana_NN.pptx`, `Bases_semana_NN.xlsx` (las 3 bases con los
encabezados del Excel) y `Gasto_semana_NN.log.json`. Si una validación falla NO se entrega el archivo
bueno: queda `..._CON_ERRORES.pptx` solo para inspección y el motivo en el error.

## Cómo funciona

1. `bases.py` — de `aggs/pdc.parquet` arma **BD Acumulado** (sem ≤ N), **BD Semana** (sem = N) y
   **BD Semana anterior** (sem = N-1): `Agrupa 1 | División | Subdirección / Territorio | Grupo de Cuentas |
   Cuentas | Real 2025. | Plan 2026 | Real 2026 | Grupo`. Pesos constantes, en miles, gasto negativo.
   `Grupo` = cruce de `Subdirección / Territorio` con `catalogo_territorio.csv`.
   Homologaciones: territorio «Sin agrupar» → nombre de la División; grupo «Renta, Luz y Teléfono» → la cuenta.
2. `xl_eval.py` — evalúa las fórmulas (SUMIFS/IFERROR/ABS/SUM) de `plantilla/Gasto_plantilla_PSA.xlsx`; la
   lógica de las láminas vive en ese Excel, no en Python.
3. `ppt_fill.py` — edita solo el texto de `plantilla/Gasto_plantilla.pptx` (celdas, tarjetas KPI, semana,
   fechas). Una celda es dinámica si su equivalente en la hoja L## es fórmula. Cambia color/negrita solo si
   cambia el signo (negativo: variación rojo `D10609` + negrita; base `E00000`).
4. `validar.py` — cuadre de láminas contra las BD, estilos permitidos, sin semanas viejas, estructura idéntica.

5. `ajustes.py` — diferencias del formato vigente respecto de la plantilla (corre al final, tras validar):
   quita las láminas de la plantilla nº 3, 4, 6, 8, 10, 13, 17, 21 y 25 (KPI repetido y «Integrador» semana de
   Total; «Integrador» semana de Centro/Norte/Sur/Otros; «Integrador» Total acumulado) y los botones de menú que
   enlazaban a ellas (el menú sube); agrega después de la lámina KPI semana de cada división (12, 16, 20, 24) una
   lámina «Detalle por cuenta» (16 grupos de cuentas de la hoja L13, calculados de las BD con las mismas reglas que
   el Excel). Verifica que el total por cuenta = tarjeta KPI. Variación % ≥ 1,000% se muestra «n.a.». Resultado: 64 láminas.
   Además agrega el bloque «Últimas 4 semanas acumuladas» entre la semana y el acumulado (Total, divisiones con su
   detalle por cuenta y cada territorio): copia las láminas de semana y las llena con BD de 4 semanas
   (`construir_bases(n=4)`, mismas fórmulas del Excel; comparativo = 4 semanas previas). Resultado: 94 láminas.
   `generar(..., ajustar=False)` la omite (lo usa `prueba_oro`).
   Nota: «No Clasificado» (catálogo → Otros) no tiene fila en el Excel de Otros; el detalle por cuenta de Otros lo excluye.

## Si cambia algo

- **Territorio nuevo / renombrado:** agregarlo a `catalogo_territorio.csv`. Si trae importe y no está, el
  proceso se detiene (el VLOOKUP del Excel lo habría dejado fuera de las láminas sin avisar).
- **Lámina nueva o tabla distinta:** actualizar las DOS plantillas (pptx y xlsx) con la nueva semana aprobada
  y volver a correr `prueba_oro`. Los cortes de tabla deben coincidir con las fórmulas de la hoja `L##`.
- **Semana de plantilla:** se detecta sola de la portada; no hay que editar nada al cambiar de semana.

## Las dos presentaciones con un comando

`python generar_presentaciones.py` genera físicos (tablero) y digitales (Excel más reciente en Descargas / Gasto semanal) en `salidas/presentacion/`; `actualizar.py` también corre ambas (aisladas, sin romper la actualización). Digitales solo: `python generar_ppt_digitales.py`. Requiere `pip install python-pptx` (ya instalado).

## Canales digitales (aparte, desde el Excel oficial)

```
cd codigo
python -m ppt_semanal.digitales_ppt --excel "<Gastos Red GARG (sem NN)....xlsx>" --semana 42
```

Genera `salidas/presentacion/Gasto_digitales_semana_NN.pptx` (+ `.validacion.json`) con el mismo diseño que el de
físicos (se toma de `Gasto_semana_NN.pptx` como plantilla visual). Cifras = hojas 8C / Temporalidad del Excel
(Shopinbaz, Baz Digital, Aurora Digital, DG Canales Digitales + Consolidado = suma de las 4). Requiere `python-pptx`
(no está en requirements.txt). Valida: suma de 4 canales = «Total Digital» del Excel y variaciones recalculadas = las del Excel.
Láminas (19): portada + por Resumen/Consolidado/4 canales: Semana, Últimas 4 semanas y Acumulado (KPI + Grupo de Cuenta; sin detalle por cuenta ni temporalidad). 4 semanas = acumulado(N) − acumulado(N-4), toma solo el Excel `(sem N-4)` de la carpeta Gasto semanal (o `--excel-previo`). Fechas automáticas: sem 1 = 29 dic 2025 – 4 ene 2026.

Fuente SQL (semanas sin Excel): `python -m ppt_semanal.extraer_digitales` baja R26/P26/R25 DIGITALES a `datos/digitales/` (2026-10-19: 111 CECOs, cuadra con el Excel: semana exacta, acumulado Real ~0.2% menos) y `python generar_ppt_digitales.py --fuente sql --semana 41` arma la presentación (`digitales_sql.py`; grupo = catálogo de grupo de cuentas; sin Total Red/Canales, % sobre Total Digital; 4 semanas directo de las tablas).

## Consolidada (físicos + digitales en un solo pptx)

Automática: `python generar_consolidada.py [--semana N]` (la corre `actualizar.py` y `generar_presentaciones.py`, después de físicos y digitales). Usa la fuente SQL; con el Excel las filas de grupo no alinean con físicos (assert en `sumar`).

Manual: `python -m ppt_semanal.digitales_ppt --consolidar --semana 42 --fuente sql`.

**Botón del tablero (Resumen):** «Descargar presentaciones (3)» baja `Presentaciones_semana_NN.zip` con los tres pptx de la semana vigente (físicos, digitales, consolidada). Requiere `puede_presentacion` y que los tres archivos existan.

**Entorno:** el tablero corre con `<DEV_ROOT>\python_env_py312` y `actualizar.py` / `generar_presentaciones.py` con el intérprete que los lanza; ambos necesitan `python-pptx`. Toma `Gasto_semana_NN.pptx` (debe existir) y le anexa las láminas de digitales → `Gasto_consolidado_semana_NN.pptx`. Portada con 2 botones y, en cada lámina, botones «Canales físicos» / «Canales digitales» al pie del menú lateral (`consolidada.py`).

## Notas

- La semana anterior sale del mismo corte de datos (se reprocesa con él), como en el Excel original.
- Fechas: «Del 21 al 27 de septiembre»; si la semana cruza de mes «Del 28 sep al 4 oct»; acumulado
  «Del 29 de dic 2025 al 27 de sep».
- Las plantillas nunca se modifican. Sin dependencias nuevas (solo lxml, openpyxl, duckdb, pandas).



