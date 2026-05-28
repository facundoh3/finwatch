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
    initial_sidebar_state="expanded",
)

_TICKERS_PATH = Path(__file__).parent.parent / "config" / "tickers.yaml"
_PORTFOLIO_FILE = Path(__file__).parent.parent / "config" / "portfolio.json"
_ANALYSIS_FILE = Path(__file__).parent.parent / "config" / "last_analysis.json"
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
    if not _ANALYSIS_FILE.exists():
        return None
    try:
        data = json.loads(_ANALYSIS_FILE.read_text())
        saved_at = datetime.fromisoformat(data["saved_at"])
        if (datetime.now() - saved_at).total_seconds() > _ANALYSIS_TTL_HOURS * 3600:
            return None
        from core.models.recommendation import AgentContext, RecommendationSet
        return AgentContext.model_validate(data["ctx"]), RecommendationSet.model_validate(data["recs"]), saved_at
    except Exception:
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


def _sidebar(cfg: dict) -> tuple[list[str], list[str], bool]:
    st.sidebar.title("📈 finwatch")
    st.sidebar.caption("Asistente personal de finanzas")
    st.sidebar.divider()

    st.sidebar.subheader("🇺🇸 Índices y ETFs")
    all_indices = cfg.get("indices_usa", []) + cfg.get("commodities", []) + cfg.get("sectores_usa", [])
    tickers_etf = st.sidebar.multiselect(
        "Índices / Sectores / Commodities",
        options=all_indices,
        default=cfg.get("indices_usa", [])[:3] + cfg.get("commodities", [])[:2],
    )

    st.sidebar.subheader("🏢 Acciones USA")
    acciones_usa = cfg.get("acciones_usa", [])
    tickers_acciones = st.sidebar.multiselect(
        "Acciones individuales",
        options=acciones_usa + ["META", "GOOGL", "JPM", "BAC", "AMD"],
        default=acciones_usa[:3],
    )

    st.sidebar.subheader("🇦🇷 Argentina")
    byma_opts = cfg.get("tickers_byma", [])
    tickers_byma = st.sidebar.multiselect(
        "BYMA / ADRs",
        options=byma_opts + cfg.get("tickers_arg_adr", []),
        default=byma_opts[:3],
    )

    st.sidebar.divider()
    force_refresh = st.sidebar.button("🔄 Analizar ahora", use_container_width=True)
    st.sidebar.divider()
    _render_market_clock()
    _render_news_cache_status()
    st.sidebar.caption("IA: Groq (gratis) · OpenRouter fallback")

    tickers_usa = tickers_etf + tickers_acciones
    return tickers_usa, tickers_byma, force_refresh


def _check_settings():
    from config.settings import get_settings
    s = get_settings()
    issues = []
    has_ai = s.groq_api_key or s.anthropic_api_key or s.openrouter_api_key
    if not s.groq_api_key:
        issues.append("⚠️ **GROQ_API_KEY** no configurada — recomendada (gratis en console.groq.com, muy rápida)")
    if not has_ai:
        issues.append("❌ Se necesita al menos **GROQ_API_KEY** o **OPENROUTER_API_KEY** para análisis de IA")
    if not s.finnhub_api_key:
        issues.append("⚠️ **FINNHUB_API_KEY** no configurada — datos de mercado limitados")
    if not s.marketaux_api_key:
        issues.append("⚠️ **MARKETAUX_API_KEY** no configurada — menos fuentes de noticias")
    return issues


def _render_news_cache_status():
    try:
        from core.services.market_calendar import get_last_close_date, minutes_until_next_close
        close_date = get_last_close_date()
        mins = minutes_until_next_close()
        if mins is not None:
            h, m = divmod(mins, 60)
            st.sidebar.caption(f"📰 Noticias: cierre {close_date} · actualiza en {h}h {m:02d}m")
        else:
            st.sidebar.caption(f"📰 Noticias: cierre {close_date}")
    except Exception:
        pass


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
    cfg = _load_tickers_config()
    tickers_usa, tickers_byma, force_refresh = _sidebar(cfg)

    if "analysis_result" not in st.session_state:
        loaded = _load_last_analysis()
        if loaded:
            ctx_cached, recs_cached, saved_at = loaded
            st.session_state["analysis_result"] = (ctx_cached, recs_cached)
            st.session_state["analysis_age"] = saved_at

    st.title("📈 finwatch")
    st.caption("Tu asistente personal de finanzas — mercados USA y Argentina")

    issues = _check_settings()
    if issues:
        with st.expander("⚙️ Configuración incompleta", expanded=True):
            for issue in issues:
                st.markdown(issue)
            st.info("Editá `.env` con tus API keys y reiniciá la app.")

    if not tickers_usa and not tickers_byma:
        st.info("Seleccioná tickers en la barra lateral y hacé clic en **Analizar ahora**.")
        _show_welcome()
        return

    if force_refresh:
        with st.spinner("Analizando mercados... (~30 segundos)"):
            try:
                from agents.orchestrator import analyze
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
            except Exception as e:
                st.error(f"Error al ejecutar el análisis: {e}")
                return

    if "analysis_result" not in st.session_state:
        _show_welcome()
        st.info("Seleccioná tus tickers y hacé clic en **🔄 Analizar ahora** para comenzar.")
        return

    ctx, recs = st.session_state["analysis_result"]

    if "analysis_age" in st.session_state:
        age = datetime.now() - st.session_state["analysis_age"]
        h = int(age.total_seconds()) // 3600
        m = (int(age.total_seconds()) % 3600) // 60
        age_str = f"{h}h {m}m" if h > 0 else f"{m}m"
        st.caption(f"📊 Análisis de hace {age_str} · Hacé clic en 🔄 para actualizar (se auto-renueva a las {_ANALYSIS_TTL_HOURS}hs)")

    _render_portfolio_banner(recs, ctx)

    if recs.market_summary:
        st.info(f"**Panorama del mercado hoy:** {recs.market_summary}")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("✅ Comprar", len(recs.by_action("BUY")))
    col2.metric("⏳ Esperar", len(recs.by_action("WAIT")))
    col3.metric("❌ Evitar", len(recs.by_action("AVOID")))
    col4.metric("📰 Noticias", len(ctx.news.items))

    st.divider()

    tab1, tab2, tab3, tab4 = st.tabs(["💡 Recomendaciones", "📊 Precios", "📰 Noticias", "💼 Mi Portafolio"])

    with tab1:
        _render_recomendaciones(recs, ctx)
    with tab2:
        _render_precios(ctx, recs)
    with tab3:
        _render_noticias(ctx)
    with tab4:
        _render_portfolio_tab(recs, ctx)


def _show_welcome():
    st.markdown("""
    ### ¿Qué hace finwatch?
    - 📊 Monitorea índices, commodities, sectores y acciones en USA y Argentina
    - 📰 Analiza noticias financieras con IA y explica su impacto
    - 💡 Te dice qué **comprar, esperar o evitar** hoy
    - 💼 Registrá tus compras y recibí alertas cuando tus posiciones estén en riesgo
    - 🔍 Cubre oro, petróleo, real estate, tecnología, energía y más
    """)


def _render_portfolio_banner(recs, ctx):
    portfolio = _load_portfolio()
    if not portfolio or not recs.recommendations:
        return

    alerts = []
    for ticker in portfolio:
        rec = next((r for r in recs.recommendations if r.ticker == ticker), None)
        snap = ctx.market.get(ticker)
        if rec and rec.action.value == "AVOID":
            alerts.append(f"🔴 **{ticker}**: el modelo recomienda EVITAR — revisá tu posición")
        elif snap and snap.change_pct <= -3:
            alerts.append(f"⬇️ **{ticker}**: bajó {snap.change_pct:.1f}% hoy — posible dip para sumar si el outlook es positivo")
        elif snap and snap.sma20 and snap.current_price < snap.sma20:
            alerts.append(f"🟡 **{ticker}**: precio cayó bajo SMA20 — tendencia débil, monitoreá")

    if alerts:
        with st.expander("⚠️ Alertas de tu portafolio", expanded=True):
            for a in alerts:
                st.markdown(a)


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

    for rec in sorted_recs:
        is_owned = rec.ticker in portfolio
        display = rec.to_display_dict()
        colors = {"BUY": "#1a4a1a", "WAIT": "#4a3a00", "AVOID": "#4a1010"}
        bg = colors.get(rec.action.value, "#222")

        snap = ctx.market.get(rec.ticker)
        price_info = f" · ${snap.current_price:.2f} ({snap.change_pct:+.1f}%)" if snap else ""
        owned_icon = " ⭐" if is_owned else ""

        with st.expander(
            f"{display['action_label']} **{rec.ticker}**{price_info}{owned_icon} — {display['confidence_label']}",
            expanded=(rec.action.value == "BUY" or is_owned),
        ):
            st.markdown(
                f"<div style='background:{bg};padding:12px;border-radius:8px;margin-bottom:8px'>"
                f"<p style='margin:0'>{rec.reasoning}</p>"
                f"</div>",
                unsafe_allow_html=True,
            )
            if display["wait_info"]:
                st.info(f"⏳ {display['wait_info']}")
            if rec.sources:
                st.markdown("**Fuentes:**")
                for s in rec.sources[:3]:
                    st.markdown(f"- {s}")

            st.divider()

            if is_owned:
                _render_position_summary(rec.ticker, portfolio[rec.ticker], snap)
                if st.button(f"Quitar {rec.ticker} del portafolio", key=f"del_{rec.ticker}"):
                    p = _load_portfolio()
                    p.pop(rec.ticker, None)
                    _save_portfolio(p)
                    st.rerun()
            elif rec.action.value in ("BUY", "WAIT"):
                _render_buy_form(rec.ticker)


def _render_position_summary(ticker: str, pos: dict, snap):
    today = date.today()
    date_bought = date.fromisoformat(pos["date_bought"])
    days_elapsed = (today - date_bought).days
    days_remaining = max(0, pos["days_to_hold"] - days_elapsed)
    usd_amount = pos["ars_amount"] / pos["usd_rate"] if pos.get("usd_rate") else 0

    col1, col2, col3 = st.columns(3)
    col1.metric("💰 Invertido", f"${pos['ars_amount']:,.0f} ARS", f"≈ ${usd_amount:.0f} USD @ ${pos.get('usd_rate', 0):.0f}")
    col2.metric("📅 Plazo", f"Día {days_elapsed} de {pos['days_to_hold']}", f"{days_remaining}d restantes")
    if snap:
        col3.metric("📈 Precio hoy", f"${snap.current_price:.2f}", f"{snap.change_pct:+.1f}%")

    if days_remaining == 0:
        st.warning("⏰ Se cumplió el plazo estimado — revisá si es momento de salir.")
    elif snap and snap.change_pct <= -3:
        st.info(f"⬇️ Bajó {snap.change_pct:.1f}% hoy — si el análisis sigue siendo positivo, podría ser un buen momento para sumar.")


def _render_buy_form(ticker: str):
    with st.expander(f"📥 Registrar compra de {ticker}"):
        ars_amount = st.number_input(
            "¿Cuánto invertiste? (ARS)",
            min_value=0, step=1000, key=f"ars_{ticker}",
        )
        usd_rate = st.number_input(
            "Tipo de cambio ARS/USD al momento de la compra",
            min_value=1.0, value=1050.0, step=10.0, key=f"rate_{ticker}",
        )
        days_to_hold = st.slider(
            "¿Cuántos días pensás mantenerla?",
            1, 365, 30, key=f"days_{ticker}",
        )
        if ars_amount > 0 and usd_rate > 0:
            st.caption(f"≈ ${ars_amount / usd_rate:.0f} USD invertidos")

        if st.button(f"✅ Guardar compra de {ticker}", key=f"save_{ticker}"):
            if ars_amount > 0:
                p = _load_portfolio()
                p[ticker] = {
                    "ars_amount": float(ars_amount),
                    "usd_rate": float(usd_rate),
                    "days_to_hold": int(days_to_hold),
                    "date_bought": date.today().isoformat(),
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
            "En la pestaña **💡 Recomendaciones**, cuando veas BUY o WAIT, "
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

        if rec and rec.action.value == "AVOID":
            icon, alert_fn, alert_msg = "🔴", st.error, "El modelo recomienda EVITAR — considerá reducir o salir de la posición"
        elif snap and snap.change_pct <= -3:
            icon, alert_fn, alert_msg = "⬇️", st.info, f"Bajó {snap.change_pct:.1f}% hoy — posible dip para sumar si el outlook es positivo"
        elif snap and snap.sma20 and snap.current_price < snap.sma20:
            icon, alert_fn, alert_msg = "🟡", st.warning, "Precio bajo SMA20 — tendencia débil, prestá atención"
        elif days_remaining == 0:
            icon, alert_fn, alert_msg = "⏰", st.warning, "Se cumplió el plazo estimado — ¿es momento de salir?"
        else:
            icon, alert_fn, alert_msg = "🟢", None, None

        with st.expander(
            f"{icon} **{ticker}** — Día {days_elapsed} de {pos['days_to_hold']} · {days_remaining}d restantes",
            expanded=True,
        ):
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

    st.dataframe(rows, use_container_width=True, hide_index=True)

    st.divider()
    tickers = [s.ticker for s in ctx.market.snapshots]
    owned_in_list = [t for t in tickers if t in portfolio]
    default_idx = tickers.index(owned_in_list[0]) if owned_in_list else 0
    col_sel, col_per = st.columns([3, 1])
    with col_sel:
        selected = st.selectbox("📊 Ver gráfico de:", tickers, index=default_idx, key="chart_ticker")
    with col_per:
        period_map = {"1 Sem": 7, "1 Mes": 30, "60 Días": 60}
        period_label = st.radio("Período", list(period_map.keys()), index=2, key="chart_period")
        days = period_map[period_label]
    if selected:
        rec = next((r for r in recs.recommendations if r.ticker == selected), None) if recs else None
        _render_price_chart(selected, days=days, rec=rec)


def _render_rec_banner(rec) -> None:
    icons = {"BUY": "✅", "WAIT": "⏳", "AVOID": "❌"}
    bg = {"BUY": "#1a4a1a", "WAIT": "#4a3a00", "AVOID": "#4a1010"}
    action = rec.action.value
    label = f"{icons.get(action, '')} {action}"
    if rec.wait_days:
        label += f" — esperar {rec.wait_days}d"
    reasoning = (rec.reasoning[:160] + "…") if rec.reasoning and len(rec.reasoning) > 160 else rec.reasoning or ""
    st.markdown(
        f"<div style='background:{bg.get(action,'#333')};padding:8px 14px;border-radius:6px;margin-bottom:4px'>"
        f"<b>{label}</b>"
        + (f"<br><small style='opacity:0.85'>{reasoning}</small>" if reasoning else "")
        + "</div>",
        unsafe_allow_html=True,
    )


def _render_price_chart(ticker: str, days: int = 60, rec=None) -> None:
    # Siempre descarga 60 días; el período solo hace zoom (TradingView style)
    cache_key = f"chart_{ticker}"
    if cache_key not in st.session_state:
        with st.spinner(f"Cargando historial de {ticker}..."):
            from core.services.chart_service import get_price_history
            st.session_state[cache_key] = get_price_history(ticker, days=60)

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

        fig.update_layout(
            title=f"{ticker} — últimos {days} días",
            height=520,
            xaxis_rangeslider_visible=False,
            legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="left", x=0),
            paper_bgcolor="#0e1117",
            plot_bgcolor="#0e1117",
            font=dict(color="#fafafa"),
            margin=dict(l=0, r=0, t=40, b=0),
        )
        fig.update_xaxes(gridcolor="#2a2a2a", showgrid=True)
        fig.update_yaxes(gridcolor="#2a2a2a", showgrid=True)
        fig.update_yaxes(title_text="Precio (USD)", row=1, col=1)
        fig.update_yaxes(title_text="Volumen", row=2, col=1)

        # Zoom por período sobre los 60 días descargados
        last_date = df.index[-1]
        start_date = last_date - timedelta(days=days)
        fig.update_xaxes(range=[str(start_date.date()), str((last_date + timedelta(days=1)).date())])

        if rec:
            _render_rec_banner(rec)

        st.plotly_chart(fig, use_container_width=True)

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
    