# -*- coding: utf-8 -*-
"""
dash_app/services/pdf_style.py
Estilo de los PDF de Resumen y Tabla Dinámica (se imprimen con Chrome, ver pdf_service.py).
Paleta copiada de Spend Legacy: rojo Aurora #a93226, Real 2026 #f1948a, gris #ccd1d9,
texto #1e2430, bordes #e2e6ed, fondos #f4f6f9, encabezado de tabla #dde1e6.
"""

PDF_CSS = """
@page { size: A4 landscape; margin: 9mm 8mm; }
* { box-sizing: border-box; }
body { font-family: "Segoe UI", Arial, sans-serif; font-size: 8pt; color: #1e2430; margin: 0; }

/* Encabezado: logo a la izquierda, título y periodo */
.enc { display: flex; align-items: center; gap: 14px; padding: 4px 0 10px 0;
       border-bottom: 2px solid #a93226; margin-bottom: 10px; }
.enc img { height: 44px; }
.kicker { font-size: 6.8pt; letter-spacing: 0.14em; font-weight: 700; color: #a93226; text-transform: uppercase; }
h1 { font-size: 16pt; font-weight: 700; margin: 2px 0 0 0; color: #1e2430; }
.sub { font-size: 8pt; color: #475569; margin-top: 3px; }
.nofilt { font-size: 7pt; color: #64748b; margin: 0 0 8px 0; }
.pie { margin-top: 10px; font-size: 6.5pt; color: #64748b; border-top: 1px solid #e2e6ed; padding-top: 5px; }

/* Títulos de sección */
h2 { font-size: 8.5pt; text-transform: uppercase; letter-spacing: 0.08em; color: #475569;
     margin: 14px 0 6px 0; padding-bottom: 4px; border-bottom: 1px solid #e2e6ed; }
h2.pagebreak { page-break-before: always; }
h3 { font-size: 8.5pt; color: #184f95; margin: 10px 0 4px 0; }

/* Tarjetas KPI (mismo criterio que el dash: franja superior de color) */
.kpis { display: flex; gap: 10px; margin: 4px 0 6px 0; }
.kpi { flex: 1; background: #ffffff; border: 1px solid #e2e6ed; border-top: 1px solid #e2e6ed;
       border-radius: 8px; padding: 9px 12px; }
.kpi:first-child { border-top: 3px solid #a93226; }
.vt-kpi-tit { font-size: 6.5pt; letter-spacing: 0.07em; text-transform: uppercase; color: #64748b; font-weight: 700; }
.vt-kpi-val { font-size: 15pt; font-weight: 800; color: #a93226; margin-top: 3px; }
.kpi.plan .vt-kpi-val, .kpi:nth-child(n+2) .vt-kpi-val { color: #1e2430; }
.vt-kpi-sig { margin-top: 4px; font-size: 7.2pt; font-weight: 700; }
.vt-kpi-et { font-weight: 500; color: #64748b; font-size: 6.8pt; }
.vt-sig-alza { color: #a93226; }
.vt-sig-baja { color: #1e7d43; }
.vt-sig-cero { color: #64748b; }

/* Tablas de detalle */
table.dat { width: 100%; border-collapse: collapse; font-size: 6.6pt; margin-bottom: 8px; }
table.dat thead { display: table-header-group; }
table.dat tr { break-inside: avoid; }
table.dat th { background-color: #dde1e6; color: #1e2430; font-weight: 700; padding: 4px 5px;
               border-bottom: 1px solid #ccd1d9; text-align: right; white-space: nowrap; }
table.dat td { padding: 3px 5px; border-bottom: 1px solid #eef0f4; text-align: right; white-space: nowrap; }
table.dat th:first-child, table.dat td:first-child { text-align: left; }
table.dat td.num { color: #a93226; font-weight: 600; }
table.dat th.r26-head { background-color: #ffe9a8; }
table.dat td.r26 { background-color: #fffbeb; font-weight: 600; }
table.dat td.var-up { color: #78281f; }
table.dat td.var-down { color: #145a32; }
table.dat tr.subtot td { background-color: #f8fafc; color: #1e2430; font-weight: 700; border-top: 1px solid #ccd1d9; }
table.dat tr.subtot td.num { color: #a93226; }
table.dat tr.totrow td { background-color: #eef1f5; color: #1e2430; font-weight: 700; border-top: 1.5px solid #475569; }
table.dat tr.totrow td.num { color: #a93226; }
table.dat tr.totrow td.r26 { background-color: #ffe9a8; }

/* Tablas de variaciones */
table.var { width: 100%; border-collapse: collapse; font-size: 7pt; margin-bottom: 6px; }
table.var th, table.var td { border-bottom: 1px solid #e2e6ed; padding: 4px 6px; }
table.var th { background-color: #dde1e6; color: #1e2430; text-align: left; }
table.var th:nth-child(2), table.var td:nth-child(2),
table.var th:nth-child(3), table.var td:nth-child(3) { text-align: right; }
table.var th:nth-child(1), table.var td:nth-child(1) { width: 18%; }
table.var th:nth-child(2), table.var td:nth-child(2) { width: 12%; }
table.var th:nth-child(3), table.var td:nth-child(3) { width: 8%; }
table.var th:nth-child(4), table.var td:nth-child(4) { width: 62%; }
"""
