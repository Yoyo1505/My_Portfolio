import pytest

from assistant.cashi import answer, parse
from core import queries as Q
from core.db import meta

GOLD = [
    ("How much did the North division spend in Q2?", "total", {"divisions": ["North"], "period": "Q2"}),
    ("total spend in Technology", "total", {"groups": ["Technology"]}),
    ("What did store 1032 cost in March?", "total", {"stores": ["Riverton Plaza 1032"], "period": "March"}),
    ("how much did we spend by division in Q2", "total", {"period": "Q2"}),
    ("Variance vs plan for Maintenance in the last 8 weeks", "vs_plan", {"groups": ["Maintenance"], "period": "last 8 weeks"}),
    ("is South over budget?", "vs_plan", {"divisions": ["South"]}),
    ("compare Corporate against last year", "vs_prior", {"divisions": ["Corporate"]}),
    ("Payroll year over year in Central", "vs_prior", {"groups": ["Payroll"], "divisions": ["Central"]}),
    ("Top 5 stores over plan in Logistics", "top_overspend", {"groups": ["Logistics"]}),
    ("top three accounts over plan", "top_overspend", {}),
    ("which regions exceeded plan the most", "top_overspend", {}),
    ("Weekly trend of Payroll in the South division", "trend", {"groups": ["Payroll"], "divisions": ["South"]}),
    ("show the trend for Cloud services", "trend", {"accounts": ["Cloud services"]}),
    ("Which anomalies happened in week 30?", "anomalies", {"period": "week 30"}),
    ("any unusual spikes in Logistics?", "anomalies", {"groups": ["Logistics"]}),
    ("Forecast for the full year in Technology", "forecast", {"groups": ["Technology"]}),
    ("where will Marketing close the year?", "forecast", {"groups": ["Marketing"]}),
    ("Who owns Cloud services?", "owner", {"accounts": ["Cloud services"]}),
    ("who is responsible for Marketing", "owner", {"groups": ["Marketing"]}),
    ("how many stores in Highlands", "list_entities", {"regions": ["Highlands"]}),
    ("help", "help", {}),
    ("what can you do?", "help", {}),
]


@pytest.mark.parametrize("question,intent,ents", GOLD)
def test_gold_intent_and_entities(question, intent, ents):
    p = parse(question)
    assert p.intent == intent
    for k, v in ents.items():
        assert p.entities.get(k) == v, (k, p.entities)


def test_intent_accuracy_report():
    ok = sum(parse(q).intent == i for q, i, _ in GOLD)
    assert ok / len(GOLD) >= 0.95


def test_numbers_match_the_query_layer():
    a = answer("How much did the North division spend in Q2?")
    expected = Q.kpis(Q.Filters(divisions=["North"], week_from=14, week_to=26))["actual"]
    assert Q.money(expected) in a.text


def test_variance_sentence_matches_kpis():
    a = answer("variance vs plan for Maintenance")
    k = Q.kpis(Q.Filters(groups=["Maintenance"]))
    assert Q.money(k["var_plan"]) in a.text and f"{k['var_plan_pct']:+.1%}" in a.text


def test_forecast_uses_all_weeks():
    a = answer("forecast for Technology")
    k = Q.kpis(Q.Filters(groups=["Technology"]))
    assert Q.money(k["forecast"]) in a.text


def test_period_never_exceeds_cutoff():
    a = answer("spend in Q4")
    assert f"-{meta()['cutoff_week']}" in a.text


def test_unknown_question_falls_back_with_warning():
    a = answer("asdf qwerty")
    assert a.confidence < 0.5 and "not sure" in a.text


def test_typo_gets_suggestion():
    p = parse("how much did Maintanence spend")
    assert any("Maintenance" in n for n in p.notes)


def test_top_n_table_is_sorted_and_limited():
    a = answer("top 4 stores over plan")
    assert len(a.table) == 4
    assert a.table["var"].is_monotonic_decreasing
