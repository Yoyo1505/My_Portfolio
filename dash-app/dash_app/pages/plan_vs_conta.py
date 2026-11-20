# -*- coding: utf-8 -*-
"""
dash_app/pages/plan_vs_conta.py
La pestaña "Presupuesto 2027" se monta desde aqui (nombre historico).
La comparacion PLAN vs CONTA original se retiro el 2026-09-30 (sin uso desde
2026-09-15); la version completa esta en _papelera_20260930/dash_app/pages/.
"""


def layout():
    from dash_app.pages.presupuesto_2027 import layout as _ppto_layout
    return _ppto_layout()
