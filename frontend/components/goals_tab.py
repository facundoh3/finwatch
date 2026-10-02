"""Pestaña 🎯 Metas — plan de ahorro en dólares hacia auto, casa, terreno, etc."""
import asyncio
from datetime import date

import streamlit as st

from core.services.goals_service import (
    Goal,
    GoalsConfig,
    build_cost_usd,
    load_goals,
    project,
    save_goals,
)

_DEFAULT_MEP = 1550.0


@st.cache_data(ttl=900, show_spinner=False)
def _fetch_mep() -> float | None:
    from config.settings import get_settings
    from core.services.iol_client import IOLClient

    s = get_settings()
    if not (s.iol_username and s.iol_password):
        return None
    try:
        return asyncio.run(IOLClient(s.iol_username, s.iol_password).get_mep_rate())
    except Exception:
        return None


def _ars(v: float) -> str:
    return f"${v:,.0f}".replace(",", ".")


def _usd(v: float) -> str:
    return f"US${v:,.0f}".replace(",", ".")


def _duration(months: int) -> str:
    years, rem = divmod(months, 12)
    y = f"{years} año{'s' if years != 1 else ''}"
    m = f"{rem} mes{'es' if rem != 1 else ''}"
    if years and rem:
        return f"{y} y {m}"
    return y if years else m


def _init_state(cfg: GoalsConfig, mep_live: float | None) -> None:
    """Carga goals.json en session_state una sola vez; después mandan los widgets."""
    if st.session_state.get("_goals_init"):
        return
    import pandas as pd

    st.session_state.update({
        "g_mep": float(round(mep_live or _DEFAULT_MEP, 1)),
        "g_income": cfg.income_ars,
        "g_expenses": cfg.expenses_ars,
        "g_savings_ars": cfg.savings_ars,
        "g_savings_usd": cfg.savings_usd,
        "g_extra": cfg.extra_income_ars,
        "g_extra_months": int(cfg.extra_income_months),
        "g_return_pct": round(cfg.annual_return * 100, 1),
        "g_emergency": int(cfg.emergency_months),
        "g_goals_base": pd.DataFrame(
            [{"Meta": g.name, "Monto USD": g.target_usd} for g in cfg.goals],
            columns=["Meta", "Monto USD"],
        ),
        "_goals_init": True,
    })


def render_goals_tab() -> None:
    import pandas as pd

    cfg = load_goals()
    mep_live = _fetch_mep()
    _init_state(cfg, mep_live)

    st.subheader("🎯 Mis metas")
    st.caption(
        "Proyección en dólares con interés compuesto. Las metas se cumplen en orden: "
        "al llegar a una, esa plata se aparta y seguís ahorrando para la siguiente."
    )

    mep = st.number_input(
        "Dólar MEP", min_value=1.0, step=10.0, key="g_mep",
        help="Se calcula en vivo con AL30/AL30D vía IOL. Podés pisarlo a mano.",
    )
    st.caption(f"🏦 MEP en vivo desde IOL: {_ars(mep_live)}" if mep_live else "⚠️ Sin IOL: MEP cargado a mano")

    with st.expander("💵 Mis números", expanded=cfg.income_ars == 0):
        c1, c2 = st.columns(2)
        income = c1.number_input("Ingreso mensual (ARS)", min_value=0.0, step=50_000.0, key="g_income")
        expenses = c2.number_input("Gasto mensual (ARS)", min_value=0.0, step=50_000.0, key="g_expenses")
        c3, c4 = st.columns(2)
        savings_ars = c3.number_input("Ahorro actual (ARS)", min_value=0.0, step=50_000.0, key="g_savings_ars")
        savings_usd = c4.number_input("Ahorro actual (USD)", min_value=0.0, step=100.0, key="g_savings_usd")
        c5, c6 = st.columns(2)
        extra = c5.number_input(
            "Ingresos extra próximos (ARS, total)", min_value=0.0, step=100_000.0, key="g_extra",
            help="Ej: un trabajo freelance que cobrás en cuotas.",
        )
        extra_months = c6.number_input("…repartidos en (meses)", min_value=1, max_value=36, key="g_extra_months")
        c7, c8 = st.columns(2)
        annual_return = c7.slider(
            "Rendimiento anual en USD (%)", 0.0, 12.0, step=0.5, key="g_return_pct",
            help="Fondo en dólares ~3-5% · ONs corporativas ~6-8% · bonos del Estado ~10-11% (más riesgo)",
        ) / 100
        emergency_months = c8.slider("Fondo de emergencia (meses de gasto)", 0, 6, key="g_emergency")

    st.markdown("**Metas (en orden de prioridad)**")
    edited = st.data_editor(
        st.session_state["g_goals_base"],
        num_rows="dynamic",
        width="stretch",
        hide_index=True,
        key="goals_editor",
        column_config={"Monto USD": st.column_config.NumberColumn(min_value=0, step=500, format="%d")},
    )
    goals = [
        Goal(str(row["Meta"]), float(row["Monto USD"]))
        for row in edited.to_dict("records")
        if pd.notna(row["Meta"]) and str(row["Meta"]).strip() and pd.notna(row["Monto USD"]) and row["Monto USD"] > 0
    ]

    new_cfg = GoalsConfig(
        income_ars=income,
        expenses_ars=expenses,
        savings_ars=savings_ars,
        savings_usd=savings_usd,
        extra_income_ars=extra,
        extra_income_months=int(extra_months),
        annual_return=annual_return,
        emergency_months=emergency_months,
        goals=goals,
    )
    if new_cfg != cfg:
        save_goals(new_cfg)

    if income <= 0:
        st.info("Cargá tu ingreso y tu gasto mensual en **💵 Mis números** para ver la proyección.")
        return

    monthly_ars = new_cfg.monthly_savings_ars
    m1, m2, m3 = st.columns(3)
    m1.metric("Ahorro mensual", _ars(monthly_ars), f"{new_cfg.savings_rate:.0%} del ingreso")
    m2.metric("En dólares", _usd(monthly_ars / mep) + "/mes")
    m3.metric("Ya tenés", _usd(savings_usd + savings_ars / mep))

    if monthly_ars <= 0:
        st.warning("Con estos números no te queda ahorro mensual. Revisá ingreso y gasto.")
        return

    proj = project(new_cfg, mep)
    today = date.today()

    st.markdown("**¿Cuándo llego?**")
    rows = []
    for r in proj.results:
        when = r.reached_on(today)
        rows.append({
            "Meta": r.name,
            "Monto": _usd(r.target_usd),
            "Tiempo": "más de 40 años" if r.month is None else ("¡Ya lo tenés!" if r.month == 0 else _duration(r.month)),
            "Fecha estimada": when.strftime("%m/%Y") if when else "—",
        })
    st.dataframe(rows, hide_index=True, width="stretch")

    _render_balance_chart(proj.balance_usd, proj.results, today)
    _render_land_calculator()


def _render_balance_chart(balances: list[float], results, today: date) -> None:
    if len(balances) < 2:
        return
    import plotly.graph_objects as go

    xs = [date(today.year + (today.month - 1 + i) // 12, (today.month - 1 + i) % 12 + 1, 1) for i in range(len(balances))]
    fig = go.Figure(go.Scatter(x=xs, y=balances, mode="lines", name="Ahorro invertido", line={"width": 2}))
    for r in results:
        when = r.reached_on(today)
        if when:
            fig.add_vline(x=when, line_dash="dot", line_color="gray")
            fig.add_annotation(x=when, y=1, yref="paper", text=r.name, showarrow=False, textangle=-90, xanchor="left", yanchor="top")
    fig.update_layout(height=320, margin={"l": 0, "r": 0, "t": 10, "b": 0}, yaxis_title="USD", showlegend=False)
    st.plotly_chart(fig, width="stretch")
    st.caption("La línea baja cuando se cumple una meta: esa plata se aparta o se gasta.")


def _render_land_calculator() -> None:
    with st.expander("🏗️ ¿Casa usada o terreno + construir?"):
        c1, c2, c3 = st.columns(3)
        land = c1.number_input("Terreno (USD)", min_value=0.0, value=40_000.0, step=1_000.0)
        m2 = c2.number_input("Metros a construir", min_value=0.0, value=60.0, step=5.0)
        cost = c3.number_input(
            "Costo por m² (USD)", min_value=0.0, value=1_000.0, step=50.0,
            help="Mendoza, sept 2026: económica ~US$1.000/m², calidad media ~US$1.300/m²",
        )
        used = st.number_input("Casa usada comparable (USD)", min_value=0.0, value=100_000.0, step=1_000.0)
        total = build_cost_usd(land, m2, cost)
        diff = total - used
        sign = "+" if diff > 0 else "-"
        st.metric("Terreno + construcción", _usd(total), f"{sign}{_usd(abs(diff))} vs. la usada", delta_color="inverse")
