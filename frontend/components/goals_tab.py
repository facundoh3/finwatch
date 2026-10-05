"""Pestaña 🎯 Metas — plan de ahorro en dólares hacia auto, casa, terreno, etc."""
import asyncio
from datetime import date

import streamlit as st

from core.services.goals_service import (
    Goal,
    GoalsConfig,
    build_cost_usd,
    load_goals,
    plan_by_goal,
    project,
    save_goals,
    this_month,
)

_DEFAULT_MEP = 1550.0
_MONTHS_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
              "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


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


def _md(text: str) -> str:
    """Escapa "$" para que Streamlit no lo tome como fórmula LaTeX."""
    return text.replace("$", "\\$")


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
        "g_last_done": cfg.last_done_month,
        "g_goals_base": pd.DataFrame(
            [{"Meta": g.name, "Monto USD": g.target_usd, "Plazo (años)": g.years, "Cumplida": g.done}
             for g in cfg.goals],
            columns=["Meta", "Monto USD", "Plazo (años)", "Cumplida"],
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
    st.caption(_md(f"🏦 MEP en vivo desde IOL: {_ars(mep_live)}") if mep_live else "⚠️ Sin IOL: MEP cargado a mano")

    with st.expander("💵 Mis números", expanded=cfg.income_ars == 0):
        c1, c2 = st.columns(2)
        income = c1.number_input("Ingreso mensual (ARS)", min_value=0.0, step=50_000.0, key="g_income")
        expenses = c2.number_input("Gasto mensual (ARS)", min_value=0.0, step=50_000.0, key="g_expenses")
        c3, c4 = st.columns(2)
        savings_ars = c3.number_input(
            "Ahorro en pesos (Mercado Pago)", min_value=0.0, step=50_000.0, key="g_savings_ars",
            help="Lo que tenés ahorrado en pesos. Funciona como tu fondo de emergencia.",
        )
        savings_usd = c4.number_input(
            "Ahorro en dólares (IOL)", min_value=0.0, step=100.0, key="g_savings_usd",
            help="Lo que tenés invertido en dólares para tus metas (fondo en dólares, ONs, etc.).",
        )
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
        column_config={
            "Monto USD": st.column_config.NumberColumn(min_value=0, step=500, format="%d"),
            "Plazo (años)": st.column_config.NumberColumn(
                min_value=0.5, max_value=30, step=0.5,
                help="¿En cuánto tiempo la querés? Define cuánto ahorrar por mes y dónde invertir.",
            ),
            "Cumplida": st.column_config.CheckboxColumn(
                help="Tildala cuando ya la usaste (ej: compraste el auto). Deja de contar y pasás a la siguiente.",
                default=False,
            ),
        },
    )
    goals = [
        Goal(
            str(row["Meta"]),
            float(row["Monto USD"]),
            float(row["Plazo (años)"]) if pd.notna(row.get("Plazo (años)")) else None,
            bool(row.get("Cumplida")) if pd.notna(row.get("Cumplida")) else False,
        )
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
        last_done_month=st.session_state["g_last_done"],
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

    _render_this_month(new_cfg, mep)

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
    _render_plan(new_cfg, mep)
    _render_land_calculator()


def _apply_month(keep_ars: float, excess_ars: float, transfer_usd: float, month_key: str) -> None:
    st.session_state["g_savings_ars"] += keep_ars - excess_ars
    st.session_state["g_savings_usd"] += transfer_usd
    st.session_state["g_last_done"] = month_key


def _render_this_month(cfg: GoalsConfig, mep: float) -> None:
    today = date.today()
    month_key = today.strftime("%Y-%m")
    already_done = cfg.last_done_month == month_key
    a = this_month(cfg, mep)

    with st.container(border=True):
        st.markdown(f"### 🗓️ Este mes ({_MONTHS_ES[today.month - 1]})")
        if already_done:
            st.success("✅ Ya hiciste lo de este mes. El mes que viene te aparecen los pasos nuevos.")
            return

        if not a.emergency_complete:
            st.caption("Etapa: **completar el fondo de emergencia** — antes de invertir, tené un colchón para imprevistos.")
        elif a.goal:
            st.caption(f"Etapa: **ahorrar para {a.goal.name}**")

        steps = []
        if a.keep_in_mp_ars > 0:
            steps.append(
                f"Cuando cobres, dejá **{_ars(a.keep_in_mp_ars)}** en Mercado Pago para el fondo de emergencia "
                f"(vas a tener {_ars(a.emergency_after_ars)} de {_ars(a.emergency_target_ars)})."
            )
        if a.transfer_ars > 0:
            from_salary = a.transfer_ars - a.excess_mp_ars
            detail = (
                f" ({_ars(from_salary)} del sueldo + {_ars(a.excess_mp_ars)} que te sobran en Mercado Pago)"
                if a.excess_mp_ars > 0 and from_salary > 0 else ""
            )
            steps.append(
                f"Transferí **{_ars(a.transfer_ars)}** a IOL{detail}. En IOL: *Ingresar dinero* → "
                "transferencia desde Mercado Pago."
            )
            steps.append(f"En IOL, comprá **dólar MEP** con esos pesos. Te quedan unos **{_usd(a.transfer_usd)}**.")
            if a.goal and a.instrument:
                steps.append(
                    f"Con esos dólares comprá **{a.instrument.name}** ({a.instrument.examples}) "
                    f"para *{a.goal.name}*."
                )
            else:
                steps.append("Ya cubriste todas tus metas 🎉. Agregá una nueva en la tabla de metas.")
        else:
            steps.append("Este mes no hace falta pasar nada a IOL.")
        steps.append("Tocá **✅ Ya lo hice** acá abajo y tus números se actualizan solos.")
        st.markdown(_md("\n".join(f"{i}. {step}" for i, step in enumerate(steps, 1))))

        if a.instrument and "ONs" in a.instrument.name and a.transfer_ars > 0:
            st.caption(
                "💡 Algunas ONs piden un mínimo de compra. Si todavía no llegás, comprá el fondo en dólares "
                "y pasalo a la ON cuando alcances el mínimo."
            )

        if a.goal and a.emergency_complete:
            after = a.goal_saved_usd + a.transfer_usd
            st.progress(
                min(after / a.goal.target_usd, 1.0),
                text=_md(f"{a.goal.name}: {_usd(after)} de {_usd(a.goal.target_usd)} después de este mes"),
            )
            if after >= a.goal.target_usd:
                st.success(
                    f"¡Con esto completás **{a.goal.name}**! Cuando la uses, tildala como *Cumplida* "
                    "en la tabla de metas."
                )

        st.button(
            "✅ Ya lo hice",
            type="primary",
            on_click=_apply_month,
            args=(a.keep_in_mp_ars, a.excess_mp_ars, a.transfer_usd, month_key),
        )


def _render_plan(cfg: GoalsConfig, mep: float) -> None:
    st.markdown("**📋 Plan: cuánto ahorrar y dónde invertir**")
    st.caption(
        "Ahorrás un monto fijo por mes que va primero a la meta más urgente; cuando la completás, "
        "pasa a la siguiente. Cuanto más lejos está una meta, más riesgo podés tomar para que rinda más. "
        "Tu ahorro actual y los ingresos extra cubren primero las metas más urgentes."
    )
    plan = plan_by_goal(cfg, mep)
    if not plan.rows:
        st.info("Poné un **Plazo (años)** en tus metas para ver cuánto tenés que ahorrar por mes.")
        return

    needed = plan.monthly_usd * mep
    capacity = cfg.monthly_savings_ars
    c1, c2, c3 = st.columns(3)
    c1.metric(f"Necesitás ahorrar ({_usd(plan.monthly_usd)}/mes)", _ars(needed) + "/mes")
    c2.metric("Hoy ahorrás", _ars(capacity) + "/mes")
    c3.metric("Te sobra" if capacity >= needed else "Te falta", _ars(abs(capacity - needed)) + "/mes")

    def _when(r) -> str:
        if r.end_month == 0:
            return "Ya cubierta con lo que tenés"
        if r.start_month is None:
            return f"Se completa sola con intereses (mes {r.end_month})"
        return f"Mes {r.start_month} al {r.end_month}"

    st.dataframe([{
        "Meta": r.goal.name,
        "Plazo": _duration(r.months),
        "Dónde invertir": f"{r.instrument.name} — {r.instrument.examples}",
        "Rinde aprox.": f"{r.instrument.annual_return:.1%}" if r.instrument.annual_return else "≈ inflación",
        "Ya tenés": _usd(r.allocated_now),
        "Cuándo le ahorrás": _when(r),
    } for r in plan.rows], hide_index=True, width="stretch")

    if needed > capacity:
        st.warning("Con estos plazos no te alcanza. Estirá el plazo de alguna meta, bajá el monto o subí el ahorro.")
    else:
        st.success("Te alcanza. Lo que sobra sumalo a la meta en curso para llegar antes.")
    st.caption(
        "Rendimientos estimados en dólares, no garantizados. Los fondos y las ONs pueden bajar de precio "
        "en el corto plazo; por eso lo de corto plazo va en instrumentos más estables."
    )


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
