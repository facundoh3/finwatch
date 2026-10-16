"""🏠 Inicio: lo único que hace falta mirar cada semana."""
from datetime import date, timedelta

import streamlit as st

from core.services.goals_service import GoalsConfig, this_month
from core.services.home_service import (
    KIND_GOAL,
    KIND_MONTHLY,
    KIND_REVIEW,
    KIND_SELL,
    KIND_SETUP,
    first_sentence,
    goal_progress,
    is_configured,
    total_money_ars,
    weekly_actions,
)
from core.services.risk_service import stock_budget
from frontend.components.goals_tab import _MONTHS_ES, _ars, _md, _usd, apply_month

_ICONS = {KIND_SETUP: "⚙️", KIND_MONTHLY: "🗓️", KIND_SELL: "🛑", KIND_REVIEW: "👀", KIND_GOAL: "🎉"}


def _month_year(d: date) -> str:
    return f"{_MONTHS_ES[d.month - 1][:3]} {d.year}"


def render_home(cfg: GoalsConfig, mep: float, portfolio: dict, analysis, needs_setup: bool) -> None:
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    st.markdown(f"## 👋 Hola{', ' + cfg.user_name if cfg.user_name else ''}")
    st.caption(f"Tu resumen de la semana del {monday.day} de {_MONTHS_ES[monday.month - 1]}")
    if needs_setup:
        st.warning("Falta configurar algo para que la app funcione bien. Mirá **⚙️ Ajustes**.")

    ctx, recs = analysis if analysis else (None, None)
    prices = {s.ticker: s.current_price for s in ctx.market.snapshots} if ctx else {}
    rec_actions = {r.ticker: r.action.value for r in recs.recommendations} if recs else {}
    actions = weekly_actions(cfg, mep, portfolio, prices, rec_actions, today)

    with st.container(border=True):
        st.markdown("#### 📌 Esta semana")
        if not actions:
            st.success("✅ No tenés que hacer nada. Tu plan sigue su curso.")
        for a in actions:
            st.markdown(f"**{_ICONS.get(a.kind, '•')} {a.title}**")
            st.markdown(_md(a.detail))
            if a.kind == KIND_MONTHLY:
                m = this_month(cfg, mep)
                st.button(
                    "✅ Ya lo hice", key="home_done", type="primary", on_click=apply_month,
                    args=(m.keep_in_mp_ars, m.excess_mp_ars, m.transfer_usd, today.strftime("%Y-%m")),
                )
                st.caption("El paso a paso detallado está en 🎯 Mi plan.")

    if not is_configured(cfg):
        return

    total = total_money_ars(cfg, mep, portfolio)
    c1, c2 = st.columns(2)
    c1.metric(f"💰 Tu plata (≈ {_usd(total / mep)})", _ars(total))
    c2.metric(f"📥 Ahorrás por mes ({cfg.savings_rate:.0%} de lo que ganás)", _ars(cfg.monthly_savings_ars))

    st.markdown("#### 🎯 Cómo vas")
    for p in goal_progress(cfg, mep, today):
        fmt = _ars if p.currency == "ARS" else _usd
        when = " · ✅ completo" if p.ratio >= 1 else (f" · llegás aprox. en {_month_year(p.eta)}" if p.eta else "")
        st.progress(p.ratio, text=_md(f"**{p.name}**: {fmt(p.current)} de {fmt(p.target)}{when}"))

    if recs and recs.market_summary:
        st.markdown("#### 🌎 El mercado")
        st.markdown(_md(first_sentence(recs.market_summary)))
        st.caption("Más detalle en 📚 Aprender.")

    st.markdown("#### 📈 Bolsillo de acciones")
    budget = stock_budget(cfg, portfolio, mep)
    if portfolio:
        st.markdown(_md(
            f"Tenés **{len(portfolio)} acción{'es' if len(portfolio) != 1 else ''}** por {_ars(budget.invested_ars)}. "
            "El detalle está en 📚 Aprender → Mi bolsillo."
        ))
    elif budget.emergency_ok:
        st.markdown(_md(
            f"Podés usar hasta **{_ars(budget.available_ars)}** para aprender con acciones, "
            "sin tocar la plata de tus metas. Ideas en 📚 Aprender."
        ))
    else:
        st.caption("Se habilita cuando completes el fondo de emergencia. Primero lo seguro.")
