"""
finwatch — Asistente personal de finanzas
Ejecutar con: bash run.sh
"""
import asyncio
import json
import sys
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

import streamlit as st
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

st.set_page_config(
    page_title="finwatch",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="auto",
)

_TICKERS_PATH = Path(__file__).parent.parent / "config" / "tickers.yaml"
_PORTFOLIO_FILE = Path(__file__).parent.parent / "config" / "portfolio.json"
_ANALYSIS_FILE = Path(__file__).parent.parent / "config" / "last_analysis.json"
_CI_ANALYSIS_FILE = Path(__file__).parent.parent / "config" / "ci_analysis.json"
_ANALYSIS_TTL_HOURS = 8


def _load_tickers_config() -> dict:
    if _TICKERS_PATH.exists():
        return yaml.safe_load(_TICKERS_PATH.read_text())
    return {}


def _load_portfolio() -> dict:
    if _PORTFOLIO_FILE.exists():
        try:
            return json.loads(_PORTFOLIO_FILE.read_text())
        except Exception:
            return {}
    return {}


def _save_portfolio(portfolio: dict):
    _PORTFOLIO_FILE.write_text(json.dumps(portfolio, indent=2, ensure_ascii=False))


def _load_last_analysis():
    from core.models.recommendation import AgentContext, RecommendationSet

    def _try_load(path: Path, ttl_hours: float) -> tuple | None:
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            saved_at = datetime.fromisoformat(data["saved_at"])
            if (datetime.now() - saved_at).total_seconds() > ttl_hours * 3600:
                return None
            return AgentContext.model_validate(data["ctx"]), RecommendationSet.model_validate(data["recs"]), saved_at
        except Exception:
            return None

    # Prioridad: análisis local reciente > análisis de CI
    local = _try_load(_ANALYSIS_FILE, _ANALYSIS_TTL_HOURS)
    if local:
        return local
    # Fallback: análisis del CI (puede ser de hoy si no abriste la app)
    ci = _try_load(_CI_ANALYSIS_FILE, 20)  # TTL extendido — el CI ya lo evaluó en el mejor momento
    if ci:
        source_label = "CI automático"
        return ci[0], ci[1], ci[2]
    return None


def _save_last_analysis(ctx, recs):
    try:
        _ANALYSIS_FILE.write_text(json.dumps({
            "saved_at": datetime.now().isoformat(),
            "ctx": ctx.model_dump(mode="json"),
            "recs": recs.model_dump(mode="json"),
        }, ensure_ascii=False, default=str))
    except Exception:
        pass


def _run_async(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import nest_asyncio
            nest_asyncio.apply()
            return loop.run_until_complete(coro)
    except RuntimeError:
        pass
    return asyncio.run(coro)


def _age_str(saved_at: datetime) -> str:
    secs = int((datetime.now() - saved_at).total_seconds())
    h, m = secs // 3600, (secs % 3600) // 60
    return f"{h}h {m}m" if h else f"{m}m"


def _esc(text: str) -> str:
    """Escapa "$" para que Streamlit no lo tome como fórmula LaTeX."""
    return (text or "").replace("$", "\\$")


def _sidebar() -> bool:
    st.sidebar.title("📈 finwatch")
    st.sidebar.caption("Tu asistente de ahorro e inversión")
    st.sidebar.divider()
    force_refresh = st.sidebar.button(
        "🔄 Analizar ahora", width="stretch",
        help="Actualiza precios, noticias y recomendaciones (tarda unos 30 segundos).",
    )
    if "analysis_age" in st.session_state:
        st.sidebar.caption(f"Último análisis: hace {_age_str(st.session_state['analysis_age'])}")
    st.sidebar.divider()
    _render_market_clock()
    _render_economic_calendar()
    return force_refresh


def _check_settings():
    from config.settings import get_settings
    s = get_settings()
    issues = []
    has_ai = s.groq_api_key or s.anthropic_api_key or s.openrouter_api_key
    if not s.groq_api_key:
        issues.append("⚠️ **GROQ_API_KEY** no configurada — recomendada (gratis en console.groq.com, muy rápida)")
    if not s.deepseek_api_key:
        issues.append("💡 **DEEPSEEK_API_KEY** no configurada — opcional pero mejora el consenso (api.deepseek.com, muy barato)")
    if not has_ai:
        issues.append("❌ Se necesita al menos **GROQ_API_KEY** o **OPENROUTER_API_KEY** para análisis de IA")
    if not s.finnhub_api_key:
        issues.append("⚠️ **FINNHUB_API_KEY** no configurada — datos de mercado limitados")
    if not s.marketaux_api_key:
        issues.append("⚠️ **MARKETAUX_API_KEY** no configurada — menos fuentes de noticias")
    from core.services.fundamentals_service import valid_user_agent
    if not valid_user_agent(s.sec_user_agent):
        issues.append(
            "💡 **SEC_USER_AGENT** no configurado — sin esto no se ven los números de las empresas ni qué compran los "
            "grandes fondos. Agregá al .env: `SEC_USER_AGENT=Tu Nombre tu@email.com` (la SEC pide nombre y email, es gratis)"
        )
    if not (s.iol_username and s.iol_password):
        issues.append("💡 **IOL_USERNAME / IOL_PASSWORD** no configurados — sin estas credenciales los precios del mercado argentino (BCBA) vienen de fuentes menos confiables")
    return issues


def _iol_connected() -> bool:
    from config.settings import get_settings
    s = get_settings()
    return bool(s.iol_username and s.iol_password)


def _render_iol_status():
    if _iol_connected():
        st.caption("🏦 IOL: conectado — precios BCBA en tiempo real")
    else:
        st.caption("🏦 IOL: no configurado — agregá IOL_USERNAME/IOL_PASSWORD al .env")


def _render_news_cache_status():
    try:
        from core.services.market_calendar import get_last_close_date, minutes_until_next_close
        close_date = get_last_close_date()
        mins = minutes_until_next_close()
        if mins is not None:
            h, m = divmod(mins, 60)
            st.caption(f"📰 Noticias: cierre {close_date} · actualiza en {h}h {m:02d}m")
        else:
            st.caption(f"📰 Noticias: cierre {close_date}")
    except Exception:
        pass


def _render_tracker_stats():
    try:
        from core.services.tracker_service import get_accuracy_stats
        stats = get_accuracy_stats()
        if stats["total"] == 0:
            st.caption(f"🎯 Tracker: sin historial aún · {stats.get('pending', 0)} pendientes")
            return
        acc = stats["accuracy"]
        by_action = stats.get("by_action", {})
        parts = []
        for action, s in by_action.items():
            parts.append(f"{action} {s['pct']}%")
        detail = " · ".join(parts) if parts else ""
        st.caption(f"🎯 Precisión: {acc}% ({stats['correct']}/{stats['total']}) — {detail}")
        if not stats.get("reliable", False):
            st.caption("⚠️ Muestra chica — no tomar este % como precisión real todavía")
    except Exception:
        pass


def _render_economic_calendar():
    if "eco_calendar" not in st.session_state:
        try:
            from core.services.economic_calendar import get_upcoming_events
            st.session_state["eco_calendar"] = get_upcoming_events(days_ahead=10)
        except Exception:
            st.session_state["eco_calendar"] = []
    events = st.session_state.get("eco_calendar", [])
    if not events:
        return
    st.sidebar.divider()
    st.sidebar.caption("📅 **Próximos eventos macro (USA)**")
    for ev in events[:5]:
        impact = ev.get("impact", "")
        icon = "🔴" if impact == "high" else "🟡"
        event_date = ev.get("time", "")[:10]
        name = ev.get("event", "")[:35]
        st.sidebar.caption(f"{icon} {event_date} · {name}")


def _render_market_clock():
    ART = timezone(timedelta(hours=-3))
    ET = timezone(timedelta(hours=-4))
    EST = timezone(timedelta(hours=-5))

    now_utc = datetime.now(timezone.utc)
    month = now_utc.month
    et_tz = ET if 3 <= month <= 11 else EST
    now_et = now_utc.astimezone(et_tz)

    open_et = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    close_et = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    is_weekday = now_et.weekday() < 5
    market_open = is_weekday and open_et <= now_et <= close_et

    open_art = open_et.astimezone(ART).strftime("%H:%M")
    close_art = close_et.astimezone(ART).strftime("%H:%M")

    if market_open:
        mins_left = int((close_et - now_et).total_seconds() / 60)
        st.sidebar.success(f"🟢 NYSE abierto · cierra {close_art} ART ({mins_left}m)")
    elif is_weekday and now_et < open_et:
        mins_to = int((open_et - now_et).total_seconds() / 60)
        st.sidebar.info(f"🕐 NYSE abre {open_art} ART (en {mins_to}m)")
    else:
        st.sidebar.info(
            f"🔴 NYSE cerrado · reabre lunes {open_art} ART"
            if now_et.weekday() >= 4
            else f"🔴 NYSE cerrado · mañana {open_art} ART"
        )


def main():
    from frontend.components.funds_tab import render_funds_tab  # noqa: F401  (import temprano: falla rápido si falta algo)
    from frontend.components.goals_tab import (
        build_cfg,
        current_mep,
        init_plan_state,
        render_goals_editor,
        render_numbers,
        render_plan_body,
    )
    from frontend.components.home_tab import render_home
    from frontend.components.style import inject_css

    inject_css()
    cfg = _load_tickers_config()
    # Todos los tickers del yaml — el análisis siempre corre sobre todos
    tickers_usa = (
        cfg.get("indices_usa", []) +
        cfg.get("commodities", []) +
        cfg.get("sectores_usa", []) +
        cfg.get("acciones_usa", []) +
        cfg.get("tickers_arg_adr", [])
    )
    tickers_byma = cfg.get("tickers_byma", [])

    if "analysis_result" not in st.session_state:
        loaded = _load_last_analysis()
        if loaded:
            ctx_cached, recs_cached, saved_at = loaded
            st.session_state["analysis_result"] = (ctx_cached, recs_cached)
            st.session_state["analysis_age"] = saved_at

    force_refresh = _sidebar()
    if force_refresh:
        with st.spinner("Analizando mercados... (~30 segundos)"):
            try:
                from agents.orchestrator import analyze
                from core.services.tracker_service import resolve_pending, save_recommendations
                old_recs = st.session_state.get("analysis_result", (None, None))[1]
                ctx, recs = _run_async(
                    analyze(
                        tickers_usa=tickers_usa,
                        tickers_byma=tickers_byma,
                        force_refresh=True,
                    )
                )
                st.session_state["analysis_result"] = (ctx, recs)
                st.session_state["analysis_age"] = datetime.now()
                _save_last_analysis(ctx, recs)
                current_prices = {s.ticker: s.current_price for s in ctx.market.snapshots}
                resolve_pending(current_prices)
                save_recommendations(recs.recommendations, ctx.market.snapshots)
                if old_recs and old_recs.recommendations:
                    st.session_state["analysis_diff"] = _compute_diff(old_recs, recs)
                else:
                    st.session_state.pop("analysis_diff", None)
            except Exception as e:
                st.error(f"Error al ejecutar el análisis: {e}")

    analysis = st.session_state.get("analysis_result")
    issues = _check_settings()
    portfolio = _load_portfolio()

    tab_home, tab_plan, tab_learn, tab_settings = st.tabs(["🏠 Inicio", "🎯 Mi plan", "📚 Aprender", "⚙️ Ajustes"])
    # Ajustes y la tabla de metas se dibujan primero porque Inicio necesita sus valores.
    init_plan_state()
    with tab_settings:
        render_numbers()
    with tab_plan:
        goals = render_goals_editor()
    plan_cfg = build_cfg(goals)
    mep = current_mep()

    with tab_home:
        render_home(plan_cfg, mep, portfolio, analysis, needs_setup=any(i.startswith("❌") for i in issues))
    with tab_plan:
        render_plan_body(plan_cfg, mep, portfolio)
    with tab_learn:
        _render_learn(analysis, cfg)
    with tab_settings:
        _render_settings_status(issues)


_NO_ANALYSIS = (
    "Todavía no hay un análisis del mercado. Tocá **🔄 Analizar ahora** en el menú lateral "
    "(en el celular, se abre con la flechita de arriba a la izquierda)."
)


def _render_learn(analysis, tickers_cfg: dict) -> None:
    from frontend.components.funds_tab import render_funds_tab

    st.caption(
        "Todo el detalle para aprender: recomendaciones, gráficos, noticias y qué compran los grandes fondos. "
        "Tu plan de ahorro no depende de nada de esto."
    )
    ctx, recs = analysis if analysis else (None, None)
    ideas, pocket, charts, news, funds = st.tabs(["💡 Ideas", "💼 Mi bolsillo", "📊 Gráficos", "📰 Noticias", "🏦 Grandes fondos"])
    displayed = recs
    with ideas:
        if recs is None:
            st.info(_NO_ANALYSIS)
        else:
            displayed = _render_ideas(ctx, recs, tickers_cfg)
    with pocket:
        _render_portfolio_tab(recs, ctx)
    with charts:
        if ctx is None:
            st.info(_NO_ANALYSIS)
        else:
            _render_precios(ctx, displayed)
    with news:
        if ctx is None:
            st.info(_NO_ANALYSIS)
        else:
            _render_noticias(ctx)
    with funds:
        render_funds_tab()


def _render_ideas(ctx, recs, tickers_cfg: dict):
    from core.models.recommendation import RecommendationSet

    if "analysis_age" in st.session_state:
        st.caption(f"Análisis de hace {_age_str(st.session_state['analysis_age'])} · se actualiza con 🔄 Analizar ahora")
    if recs.market_summary:
        st.info(_esc(f"**El mercado hoy:** {recs.market_summary}"))

    all_tickers = sorted(set(sum((tickers_cfg.get(k, []) for k in (
        "indices_usa", "commodities", "sectores_usa", "acciones_usa", "tickers_arg_adr", "tickers_byma")), [])))
    view_filter = st.multiselect(
        "🔍 Ver solo estos tickers", options=all_tickers, default=[], placeholder="Todos",
        help="Solo filtra lo que se muestra; el análisis siempre corre sobre todos.",
    )
    displayed = recs
    if view_filter:
        displayed = RecommendationSet(
            recommendations=[r for r in recs.recommendations if r.ticker in set(view_filter)],
            market_summary=recs.market_summary,
            generated_at=recs.generated_at,
        )

    c1, c2, c3 = st.columns(3)
    c1.metric("✅ Comprar", len(displayed.by_action("BUY")))
    c2.metric("⏳ Esperar", len(displayed.by_action("WAIT")))
    c3.metric("❌ No comprar", len(displayed.by_action("AVOID")))
    _render_tracker_stats()
    _render_recomendaciones(displayed, ctx)
    return displayed


def _render_settings_status(issues: list[str]) -> None:
    st.divider()
    st.markdown("#### 🔌 Conexiones")
    if issues:
        for issue in issues:
            st.markdown(issue)
        st.caption("Se configuran en el archivo `.env`. Después de cambiarlo, reiniciá la app.")
    else:
        st.success("Todo configurado ✅")
    try:
        from config.settings import get_settings
        s = get_settings()
        consensus = (
            "Groq + DeepSeek" if (s.groq_api_key and s.deepseek_api_key)
            else "Groq + OpenRouter" if (s.groq_api_key and s.openrouter_api_key) else "Groq solo"
        )
        st.caption(f"🤖 IA: {consensus} · respaldo Claude")
    except Exception:
        pass
    _render_iol_status()
    _render_news_cache_status()


def _compute_diff(old_recs, new_recs) -> dict:
    old_map = {r.ticker: r.action.value for r in old_recs.recommendations}
    diff = {}
    for r in new_recs.recommendations:
        old_action = old_map.get(r.ticker)
        new_action = r.action.value
        if old_action and old_action != new_action:
            diff[r.ticker] = (old_action, new_action)
    return diff


@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_technical_data(tickers: tuple) -> dict:
    from core.services.chart_service import get_price_history
    result = {}
    for ticker in tickers:
        df = get_price_history(ticker, days=200, interval="1d")
        if df is not None and not df.empty:
            result[ticker] = df
    return result


def _render_technicals(ticker: str, snap) -> None:
    from core.services.technical_service import analyze as tech_analyze
    df = _fetch_technical_data((ticker,)).get(ticker)
    if df is None:
        st.caption("Sin datos del gráfico disponibles.")
        return
    high_52w = getattr(snap, "high_52w", None) if snap else None
    report = tech_analyze(ticker, df, getattr(snap, "current_price", None), high_52w)
    color = {"FUERTE": "green", "MIXTO": "orange"}.get(report.grade, "red")
    st.markdown(f"**El gráfico:** :{color}[**{report.summary}**] · {report.stage}")
    cols = st.columns(3)
    for i, sig in enumerate(report.signals):
        cols[i % 3].caption(_esc(f"{sig.icon} **{sig.name}** — {sig.detail}"))


def _render_fundamentals(fund) -> None:
    if fund is None:
        return
    from core.services.fundamentals_service import fundamental_signals

    signals = fundamental_signals(fund)
    ok = sum(s.ok for s in signals)
    color = "green" if ok == len(signals) else "orange" if ok else "red"
    derived = " · Q4 calculado del balance anual" if fund.derived else ""
    st.markdown(
        f"**Los números de la empresa (SEC):** :{color}[**{ok}/{len(signals)} O'Neil**] · "
        f"{fund.fiscal or ''} (cierre {fund.period_end}){derived}"
    )
    cols = st.columns(3)
    for i, sig in enumerate(signals):
        cols[i].caption(f"{sig.icon} **{sig.name}** — {sig.detail}".replace("$", "\\$"))
    extras = []
    if fund.revenue is not None:
        extras.append(f"ventas del trimestre US\\${fund.revenue / 1e9:,.1f} mil millones")
    if fund.eps is not None:
        extras.append(f"ganancia por acción US\\${fund.eps:.2f}")
    if fund.net_margin is not None:
        extras.append(f"margen neto {fund.net_margin:.0f}%")
    if extras:
        text = " · ".join(extras)
        st.caption(text[0].upper() + text[1:])


def _render_recomendaciones(recs, ctx):
    portfolio = _load_portfolio()

    if not recs.recommendations:
        st.warning("No hay recomendaciones. Verificá las API keys y volvé a analizar.")
        return

    action_order = {"BUY": 0, "WAIT": 1, "AVOID": 2}
    sorted_recs = sorted(
        recs.recommendations,
        key=lambda r: (0 if r.ticker in portfolio else 1, action_order.get(r.action.value, 3)),
    )

    seen_tickers: set[str] = set()
    for idx, rec in enumerate(sorted_recs):
        if rec.ticker in seen_tickers:
            continue
        seen_tickers.add(rec.ticker)

        is_owned = rec.ticker in portfolio
        display = rec.to_display_dict()

        snap = ctx.market.get(rec.ticker)
        price_info = f" · ${snap.current_price:.2f} ({snap.change_pct:+.1f}%)" if snap else ""
        owned_icon = " ⭐" if is_owned else ""

        diff = st.session_state.get("analysis_diff", {})
        diff_badge = ""
        if rec.ticker in diff:
            old_a, new_a = diff[rec.ticker]
            diff_badge = f" · **{old_a} → {new_a}** ↕"

        with st.expander(
            f"{display['action_label']} **{rec.ticker}**{price_info}{owned_icon} · {display['confidence_label']}{diff_badge}",
            expanded=(rec.action.value == "BUY" or is_owned),
        ):
            st.markdown(_esc(rec.reasoning))
            if display["wait_info"]:
                st.info(f"⏳ {display['wait_info']}")

            with st.expander("🔍 Ver por qué (indicadores)"):
                _render_technicals(rec.ticker, snap)
                _render_fundamentals(ctx.fundamentals.get(rec.ticker))
                if rec.sources:
                    st.markdown("**Fuentes:**")
                    for src in rec.sources[:3]:
                        st.markdown(f"- {src}")

            st.divider()

            if is_owned:
                _render_position_summary(rec.ticker, portfolio[rec.ticker], snap)
                if st.button(f"Quitar {rec.ticker} del portafolio", key=f"del_{rec.ticker}_{idx}"):
                    p = _load_portfolio()
                    p.pop(rec.ticker, None)
                    _save_portfolio(p)
                    st.rerun()
            elif rec.action.value in ("BUY", "WAIT"):
                _render_buy_form(rec.ticker, snap, idx)


def _render_position_summary(ticker: str, pos: dict, snap):
    today = date.today()
    date_bought = date.fromisoformat(pos["date_bought"])
    days_elapsed = (today - date_bought).days
    days_remaining = max(0, pos["days_to_hold"] - days_elapsed)
    usd_amount = pos["ars_amount"] / pos["usd_rate"] if pos.get("usd_rate") else 0

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("💰 Invertido", f"${pos['ars_amount']:,.0f} ARS", f"≈ ${usd_amount:.0f} USD")
    col2.metric("📅 Plazo", f"Día {days_elapsed} de {pos['days_to_hold']}", f"{days_remaining}d restantes")
    if snap:
        col3.metric("📈 Precio hoy", f"${snap.current_price:.2f}", f"{snap.change_pct:+.1f}%")
    stop = pos.get("stop_price")
    buy_price = pos.get("buy_price")
    if stop and snap:
        pct_to_stop = (snap.current_price - stop) / snap.current_price * 100
        col4.metric("🛑 Stop-loss", f"${stop:.2f}", f"{pct_to_stop:+.1f}% hasta stop")
    elif buy_price:
        col4.metric("💵 Precio compra", f"${buy_price:.2f}")

    if stop and snap and snap.current_price < stop:
        st.error(f"🛑 **STOP-LOSS PERFORADO** — Compraste a ${buy_price:.2f}, stop en ${stop:.2f}, precio actual ${snap.current_price:.2f}. Considerá salir de la posición.")
    elif days_remaining == 0:
        st.warning("⏰ Se cumplió el plazo estimado — revisá si es momento de salir.")
    elif snap and snap.change_pct <= -3:
        st.info(f"⬇️ Bajó {snap.change_pct:.1f}% hoy — si el análisis sigue siendo positivo, podría ser un buen momento para sumar.")


def _stock_limits(ticker: str):
    """(presupuesto, máximo para este ticker) según el plan de Metas, o None si Metas no está configurado."""
    from core.services.goals_service import load_goals
    from core.services.risk_service import max_for_ticker, stock_budget
    from frontend.components.goals_tab import current_mep

    cfg = load_goals()
    if cfg.income_ars <= 0:
        return None
    portfolio = _load_portfolio()
    budget = stock_budget(cfg, portfolio, current_mep())
    return budget, max_for_ticker(budget, portfolio, ticker)


def _render_buy_form(ticker: str, snap=None, _idx: int = 0):
    from core.services.risk_service import check_buy
    from core.services.technical_service import calc_stop_loss
    from frontend.components.goals_tab import current_mep

    _k = f"{ticker}_{_idx}"
    limits = _stock_limits(ticker)
    with st.expander(f"📥 Registrar compra de {ticker}"):
        if limits is None:
            st.caption("💡 Configurá 🎯 Metas y la app te va a decir cuánto podés poner en acciones sin tocar tus metas.")
        elif not limits[0].emergency_ok:
            st.warning("⚠️ Según tu plan, todavía no deberías comprar acciones: primero completá el fondo de emergencia.")
        else:
            from core.services.risk_service import ars
            st.info(f"📏 Según tu plan, en {ticker} podés poner hasta **{ars(limits[1])}**.".replace("$", "\\$"))

        default_price = float(snap.current_price) if snap else 0.0
        buy_price = st.number_input(
            "Precio de compra por acción (USD)",
            min_value=0.0, value=default_price, step=0.01, format="%.2f", key=f"price_{_k}",
        )
        if buy_price > 0:
            stop = calc_stop_loss(buy_price)
            st.caption(f"🛑 Stop-loss sugerido (O'Neil -7%): **${stop:.2f}** · Salí si cae por debajo de este precio.")
        ars_amount = st.number_input(
            "¿Cuánto invertiste? (ARS)",
            min_value=0, step=1000, key=f"ars_{_k}",
        )
        from frontend.components.goals_tab import live_mep
        usd_rate = live_mep()
        if usd_rate:
            st.caption(_esc(f"💵 Dólar MEP de IOL: **${usd_rate:,.2f}**".replace(",", "X").replace(".", ",").replace("X", ".")))
        else:
            usd_rate = st.number_input(
                "Dólar MEP (IOL no respondió, cargalo a mano)",
                min_value=1.0, value=float(round(current_mep())), step=10.0, key=f"rate_{_k}",
            )
        days_to_hold = st.slider(
            "¿Cuántos días pensás mantenerla?",
            1, 365, 30, key=f"days_{_k}",
        )
        if ars_amount > 0 and usd_rate > 0:
            st.caption(f"≈ ${ars_amount / usd_rate:.0f} USD invertidos")

        problems = check_buy(limits[0], _load_portfolio(), ticker, float(ars_amount)) if limits and ars_amount > 0 else []
        confirmed = True
        if problems:
            st.warning("**Esta compra se pasa de tu plan:**\n" + "\n".join(f"- {p}" for p in problems).replace("$", "\\$"))
            confirmed = st.checkbox("Ya la hice (o la quiero hacer igual): registrarla de todos modos", key=f"force_{_k}")

        if st.button(f"✅ Guardar compra de {ticker}", key=f"save_{_k}", disabled=not confirmed):
            if ars_amount > 0:
                p = _load_portfolio()
                p[ticker] = {
                    "ars_amount": float(ars_amount),
                    "usd_rate": float(usd_rate),
                    "days_to_hold": int(days_to_hold),
                    "date_bought": date.today().isoformat(),
                    "buy_price": float(buy_price) if buy_price > 0 else None,
                    "stop_price": calc_stop_loss(buy_price) if buy_price > 0 else None,
                }
                _save_portfolio(p)
                st.success(f"✅ {ticker} guardada en tu portafolio")
                st.rerun()
            else:
                st.error("Ingresá un monto mayor a 0.")


def _render_portfolio_tab(recs, ctx):
    portfolio = _load_portfolio()

    if not portfolio:
        st.info(
            "No tenés posiciones registradas. "
            "En **📚 Aprender → 💡 Ideas**, cuando veas Comprar o Esperar, "
            "podés registrar tu compra con el formulario de cada acción."
        )
        return

    st.subheader("💼 Mis posiciones")
    today = date.today()

    for ticker, pos in list(portfolio.items()):
        snap = ctx.market.get(ticker) if ctx else None
        rec = next((r for r in recs.recommendations if r.ticker == ticker), None) if recs else None

        date_bought = date.fromisoformat(pos["date_bought"])
        days_elapsed = (today - date_bought).days
        days_remaining = max(0, pos["days_to_hold"] - days_elapsed)
        usd_amount = pos["ars_amount"] / pos["usd_rate"] if pos.get("usd_rate") else 0

        stop = pos.get("stop_price")
        buy_price = pos.get("buy_price")
        stop_triggered = stop and snap and snap.current_price < stop

        if stop_triggered:
            icon, alert_fn, alert_msg = "🛑", st.error, f"STOP-LOSS PERFORADO — compraste a ${buy_price:.2f}, stop en ${stop:.2f}, precio actual ${snap.current_price:.2f}. ¡Considerá salir!"
        elif rec and rec.action.value == "AVOID":
            icon, alert_fn, alert_msg = "🔴", st.error, "El modelo recomienda EVITAR — considerá reducir o salir de la posición"
        elif snap and snap.change_pct <= -3:
            icon, alert_fn, alert_msg = "⬇️", st.info, f"Bajó {snap.change_pct:.1f}% hoy — posible dip para sumar si el outlook es positivo"
        elif snap and snap.sma20 and snap.current_price < snap.sma20:
            icon, alert_fn, alert_msg = "🟡", st.warning, "Precio bajo SMA20 — tendencia débil, prestá atención"
        elif days_remaining == 0:
            icon, alert_fn, alert_msg = "⏰", st.warning, "Se cumplió el plazo estimado — ¿es momento de salir?"
        else:
            icon, alert_fn, alert_msg = "🟢", None, None

        expander_title = f"{icon} **{ticker}** — Día {days_elapsed} de {pos['days_to_hold']} · {days_remaining}d restantes"
        if stop and snap:
            pct_to_stop = (snap.current_price - stop) / snap.current_price * 100
            expander_title += f" · Stop ${stop:.2f} ({pct_to_stop:+.1f}%)"

        with st.expander(expander_title, expanded=True):
            if alert_fn:
                alert_fn(alert_msg)

            col1, col2, col3, col4 = st.columns(4)
            col1.metric("ARS invertidos", f"${pos['ars_amount']:,.0f}")
            col2.metric("USD equiv.", f"${usd_amount:.0f}", f"@ ${pos.get('usd_rate', 0):.0f}")
            col3.metric("Días restantes", days_remaining)
            if snap:
                col4.metric("Precio actual", f"${snap.current_price:.2f}", f"{snap.change_pct:+.1f}%")

            if rec:
                action_labels = {"BUY": "✅ Sumar más", "WAIT": "⏳ Mantener — todavía no es momento", "AVOID": "❌ Salir de la posición"}
                st.info(f"**Recomendación actual:** {action_labels.get(rec.action.value, rec.action.value)}\n\n{rec.reasoning[:250]}...")

            col_emrg, col_del = st.columns([2, 1])
            with col_emrg:
                if st.button(f"⚡ Análisis de emergencia", key=f"emrg_{ticker}"):
                    with st.spinner(f"Analizando {ticker} en tiempo real..."):
                        try:
                            from agents.orchestrator import analyze_emergency
                            _, emrg_recs = _run_async(analyze_emergency([ticker]))
                            emrg_rec = next((r for r in emrg_recs.recommendations if r.ticker == ticker), None)
                            st.session_state[f"emrg_result_{ticker}"] = emrg_rec
                        except Exception as e:
                            st.error(f"Error en análisis de emergencia: {e}")

            if st.session_state.get(f"emrg_result_{ticker}"):
                er = st.session_state[f"emrg_result_{ticker}"]
                label = er.to_display_dict()["action_label"]
                box = {"BUY": st.success, "WAIT": st.warning}.get(er.action.value, st.error)
                box(_esc(f"**⚡ Análisis rápido: {label}**" + (f"\n\n{er.reasoning[:200]}…" if er.reasoning else "")))

            with col_del:
                if st.button(f"Eliminar {ticker}", key=f"ptab_del_{ticker}"):
                    p = _load_portfolio()
                    p.pop(ticker, None)
                    _save_portfolio(p)
                    st.rerun()

    st.divider()
    st.caption("Las posiciones se guardan en `config/portfolio.json` y persisten entre sesiones.")


def _render_precios(ctx, recs=None):
    portfolio = _load_portfolio()

    if not ctx.market.snapshots:
        st.info("No hay datos de precio disponibles.")
        return

    from core.models.market import PriceDirection
    rows = []
    snapshots_sorted = sorted(
        ctx.market.snapshots,
        key=lambda x: (0 if x.ticker in portfolio else 1, -abs(x.change_pct)),
    )
    for s in snapshots_sorted:
        is_owned = s.ticker in portfolio
        arrow = "▲" if s.direction == PriceDirection.UP else ("▼" if s.direction == PriceDirection.DOWN else "▶")
        color = "🟢" if s.direction == PriceDirection.UP else ("🔴" if s.direction == PriceDirection.DOWN else "⚪")
        sma_str = ""
        if s.sma20:
            rel = "↑" if s.current_price > s.sma20 else "↓"
            sma_str = f"SMA20:{rel}${s.sma20:.0f}"
        rows.append({
            "": ("⭐" if is_owned else "") + color,
            "Ticker": s.ticker,
            "Precio": f"${s.current_price:.2f}",
            "Cambio %": f"{arrow} {s.change_pct:+.2f}%",
            "SMA20": sma_str,
            "Volumen": f"{s.volume/1_000_000:.1f}M" if s.volume >= 1_000_000 else str(s.volume),
        })

    st.dataframe(rows, width="stretch", hide_index=True)

    st.divider()
    tickers = [s.ticker for s in ctx.market.snapshots]
    owned_in_list = [t for t in tickers if t in portfolio]
    default_idx = tickers.index(owned_in_list[0]) if owned_in_list else 0
    col_sel, col_per = st.columns([3, 1])
    with col_sel:
        selected = st.selectbox("📊 Ver gráfico de:", tickers, index=default_idx, key="chart_ticker")
    with col_per:
        # Cada período usa distinta granularidad — descomposición real estilo TradingView
        _PERIODS = {"1 Sem": (7, "1d"), "1 Mes": (30, "1d"), "6 Meses": (180, "1wk")}
        period_label = st.radio("Período", list(_PERIODS.keys()), index=1, key="chart_period")
        days, interval = _PERIODS[period_label]
    if selected:
        rec = next((r for r in recs.recommendations if r.ticker == selected), None) if recs else None
        portfolio = _load_portfolio()
        stop_price = portfolio.get(selected, {}).get("stop_price")
        _render_price_chart(selected, days=days, interval=interval, rec=rec, stop_price=stop_price)


def _render_rec_banner(rec) -> None:
    label = rec.to_display_dict()["action_label"]
    if rec.wait_days:
        label += f" — esperar {rec.wait_days} días"
    reasoning = (rec.reasoning[:160] + "…") if rec.reasoning and len(rec.reasoning) > 160 else rec.reasoning or ""
    box = {"BUY": st.success, "WAIT": st.warning}.get(rec.action.value, st.error)
    box(_esc(f"**{label}**" + (f"\n\n{reasoning}" if reasoning else "")))


def _render_price_chart(ticker: str, days: int = 60, interval: str = "1d", rec=None, stop_price: float | None = None) -> None:
    # Cada período descarga datos con distinta granularidad (TradingView style)
    cache_key = f"chart_{ticker}_{interval}_{days}"
    if cache_key not in st.session_state:
        with st.spinner(f"Cargando historial de {ticker}..."):
            from core.services.chart_service import get_price_history
            st.session_state[cache_key] = get_price_history(ticker, days=days, interval=interval)

    df = st.session_state.get(cache_key)
    if df is None or df.empty:
        st.warning(f"No hay datos históricos para {ticker}.")
        return

    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots

        fig = make_subplots(
            rows=2, cols=1, shared_xaxes=True,
            vertical_spacing=0.04, row_heights=[0.75, 0.25],
        )

        fig.add_trace(go.Candlestick(
            x=df.index, open=df["Open"], high=df["High"],
            low=df["Low"], close=df["Close"],
            name=ticker,
            increasing_line_color="#26a69a",
            decreasing_line_color="#ef5350",
            increasing_fillcolor="#26a69a",
            decreasing_fillcolor="#ef5350",
        ), row=1, col=1)

        if not df["SMA20"].isna().all():
            fig.add_trace(go.Scatter(
                x=df.index, y=df["SMA20"], name="SMA 20",
                line=dict(color="#ff9800", width=1.5),
            ), row=1, col=1)

        if not df["SMA50"].isna().all():
            fig.add_trace(go.Scatter(
                x=df.index, y=df["SMA50"], name="SMA 50",
                line=dict(color="#42a5f5", width=1.5),
            ), row=1, col=1)

        vol_colors = [
            "#26a69a" if c >= o else "#ef5350"
            for c, o in zip(df["Close"], df["Open"])
        ]
        fig.add_trace(go.Bar(
            x=df.index, y=df["Volume"],
            name="Volumen", marker_color=vol_colors, opacity=0.7,
        ), row=2, col=1)

        _titles = {
            (7, "1d"): f"{ticker} — última semana (diario)",
            (30, "1d"): f"{ticker} — último mes (diario)",
            (180, "1wk"): f"{ticker} — últimos 6 meses (semanal)",
        }
        _title = _titles.get((days, interval), f"{ticker} — {days}d")
        fig.update_layout(
            title=_title,
            height=520,
            xaxis_rangeslider_visible=False,
            legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="left", x=0),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=0, r=0, t=40, b=0),
        )
        fig.update_xaxes(gridcolor="rgba(127,127,127,0.2)", showgrid=True)
        fig.update_yaxes(gridcolor="rgba(127,127,127,0.2)", showgrid=True)
        fig.update_yaxes(title_text="Precio (USD)", row=1, col=1)
        fig.update_yaxes(title_text="Volumen", row=2, col=1)

        if stop_price:
            fig.add_hline(
                y=stop_price,
                line=dict(color="#ef5350", width=1.5, dash="dash"),
                annotation_text=f"Stop ${stop_price:.2f}",
                annotation_font_color="#ef5350",
                row=1, col=1,
            )

        if rec:
            _render_rec_banner(rec)

        st.plotly_chart(fig, width="stretch")

    except ImportError:
        st.warning("Instalá plotly para ver el gráfico de velas: `pip install plotly`")
        st.line_chart(df[["Close", "SMA20"]].dropna(subset=["Close"]))


def _render_noticias(ctx):
    portfolio = _load_portfolio()

    from frontend.components.news_card import render_news_card
    from core.models.news import SentimentLabel

    col1, col2 = st.columns(2)
    with col1:
        sentiment_filter = st.selectbox("Sentimiento", ["Todos", "POSITIVE", "NEGATIVE", "NEUTRAL"])
    with col2:
        tier_filter = st.selectbox("Fuente", ["Todas", "Verificadas (Tier A)", "Tier B"])

    items = ctx.news.items
    if sentiment_filter != "Todos":
        items = [n for n in items if n.sentiment_label == SentimentLabel(sentiment_filter)]
    if tier_filter == "Verificadas (Tier A)":
        items = [n for n in items if n.source_tier == "A"]
    elif tier_filter == "Tier B":
        items = [n for n in items if n.source_tier == "B"]

    if portfolio:
        items = sorted(items, key=lambda n: (
            0 if any(t in (n.related_tickers or []) for t in portfolio) else 1
        ))

    if not items:
        st.info("No hay noticias con los filtros seleccionados.")
        return

    for news in items[:30]:
        render_news_card(news)


if __name__ == "__main__":
    main()
    