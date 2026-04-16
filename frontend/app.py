"""
finwatch — App financiera personal
Entry point de Streamlit.
Ejecutar con: streamlit run frontend/app.py
"""
import asyncio
import sys
from pathlib import Path

import streamlit as st

# Agrega el root del proyecto al path para importar módulos
sys.path.insert(0, str(Path(__file__).parent.parent))

st.set_page_config(
    page_title="finwatch",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)


def _run_async(coro):
    """Helper para correr coroutines en Streamlit."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import nest_asyncio
            nest_asyncio.apply()
            return loop.run_until_complete(coro)
    except RuntimeError:
        pass
    return asyncio.run(coro)


def _sidebar() -> tuple[list[str], list[str], bool]:
    """Sidebar con selector de tickers y controles."""
    import yaml
    tickers_path = Path(__file__).parent.parent / "config" / "tickers.yaml"
    defaults = yaml.safe_load(tickers_path.read_text()) if tickers_path.exists() else {}

    st.sidebar.title("📈 finwatch")
    st.sidebar.caption("App financiera personal")
    st.sidebar.divider()

    st.sidebar.subheader("Mercado USA")
    default_usa = defaults.get("tickers_usa", ["AAPL", "NVDA", "TSLA", "MSFT", "SPY"])
    tickers_usa = st.sidebar.multiselect(
        "Acciones USA",
        options=default_usa + ["AMZN", "META", "GOOGL", "JPM", "BAC"],
        default=default_usa[:4],
    )

    st.sidebar.subheader("Mercado ARG")
    default_byma = defaults.get("tickers_byma", ["YPFD", "GGAL", "TXAR", "BBAR", "PAMP"])
    tickers_byma = st.sidebar.multiselect(
        "Acciones BYMA",
        options=default_byma,
        default=default_byma[:3],
    )

    st.sidebar.divider()
    force_refresh = st.sidebar.button("🔄 Actualizar análisis", use_container_width=True)

    st.sidebar.divider()
    st.sidebar.caption("ℹ️ Cache: 30 min | Noticias: últimas 24hs")
    st.sidebar.caption("Modelos: Qwen3.6 (contexto) + Claude Sonnet (análisis)")

    return tickers_usa, tickers_byma, force_refresh


def main():
    tickers_usa, tickers_byma, force_refresh = _sidebar()

    st.title("📈 finwatch")
    st.caption("Tu app financiera personal — mercados USA y Argentina")

    if not tickers_usa and not tickers_byma:
        st.warning("Seleccioná al menos un ticker en la barra lateral.")
        return

    if force_refresh or "analysis_result" not in st.session_state:
        with st.spinner("Analizando mercados... (puede demorar ~30 segundos)"):
            try:
                from agents.orchestrator import analyze
                ctx, recs = _run_async(
                    analyze(
                        tickers_usa=tickers_usa,
                        tickers_byma=tickers_byma,
                        force_refresh=force_refresh,
                    )
                )
                st.session_state["analysis_result"] = (ctx, recs)
            except Exception as e:
                st.error(f"Error al ejecutar el análisis: {e}")
                st.info("Verificá que las API keys estén configuradas en .env")
                return

    ctx, recs = st.session_state["analysis_result"]

    # Market summary
    if recs.market_summary:
        st.info(f"**Resumen del mercado:** {recs.market_summary}")

    # Quick stats
    col1, col2, col3, col4 = st.columns(4)
    buy_count = len(recs.by_action("BUY"))
    wait_count = len(recs.by_action("WAIT"))
    avoid_count = len(recs.by_action("AVOID"))
    news_count = len(ctx.news.items)

    col1.metric("✅ BUY", buy_count)
    col2.metric("⏳ WAIT", wait_count)
    col3.metric("❌ AVOID", avoid_count)
    col4.metric("📰 Noticias", news_count)

    st.divider()

    # Tabs de navegación
    tab1, tab2, tab3 = st.tabs(["📊 Dashboard", "📰 Noticias", "💡 Recomendaciones"])

    with tab1:
        _render_dashboard(ctx, recs)
    with tab2:
        _render_noticias(ctx)
    with tab3:
        _render_recomendaciones(recs, ctx)


def _render_dashboard(ctx, recs):
    from frontend.components.ticker_card import render_ticker_card
    from frontend.components.tradingview_widget import render_market_overview

    st.subheader("Vista general del mercado")
    render_market_overview()

    st.subheader("Tus tickers")
    snapshots = ctx.market.snapshots
    if not snapshots:
        st.info("No se pudieron obtener datos de mercado. Verificá las API keys.")
        return

    cols = st.columns(min(len(snapshots), 3))
    for i, snap in enumerate(snapshots):
        rec = recs.get(snap.ticker)
        with cols[i % 3]:
            render_ticker_card(snap, rec)


def _render_noticias(ctx):
    from frontend.components.news_card import render_news_card

    st.subheader("Noticias relevantes")

    # Filtros
    col1, col2 = st.columns(2)
    with col1:
        sentiment_filter = st.selectbox(
            "Filtrar por sentimiento",
            ["Todos", "POSITIVE", "NEGATIVE", "NEUTRAL"],
        )
    with col2:
        tier_filter = st.selectbox(
            "Filtrar por fuente",
            ["Todas", "Tier A (verificadas)", "Tier B"],
        )

    items = ctx.news.items
    if sentiment_filter != "Todos":
        from core.models.news import SentimentLabel
        items = [n for n in items if n.sentiment_label == SentimentLabel(sentiment_filter)]
    if tier_filter == "Tier A (verificadas)":
        items = [n for n in items if n.source_tier == "A"]
    elif tier_filter == "Tier B":
        items = [n for n in items if n.source_tier == "B"]

    if not items:
        st.info("No hay noticias con los filtros seleccionados.")
        return

    for news in items[:30]:
        render_news_card(news)


def _render_recomendaciones(recs, ctx):
    from frontend.components.tradingview_widget import render_mini_chart

    st.subheader("Recomendaciones de inversión")

    if not recs.recommendations:
        st.warning("No hay recomendaciones disponibles. Ejecutá el análisis primero.")
        return

    # Ordenar: BUY primero, luego WAIT, luego AVOID
    order = {"BUY": 0, "WAIT": 1, "AVOID": 2}
    sorted_recs = sorted(recs.recommendations, key=lambda r: order.get(r.action.value, 3))

    for rec in sorted_recs:
        display = rec.to_display_dict()
        action_colors = {"BUY": "#1a7a1a", "WAIT": "#665500", "AVOID": "#6a1111"}
        bg_color = action_colors.get(rec.action.value, "#333")

        with st.expander(
            f"{display['action_label']} **{rec.ticker}** — {display['confidence_label']}",
            expanded=(rec.action.value == "BUY"),
        ):
            col1, col2 = st.columns([3, 2])
            with col1:
                st.markdown(
                    f"<div style='background:{bg_color};padding:12px;border-radius:8px'>"
                    f"<h4>{display['action_label']} {rec.ticker}</h4>"
                    f"<p>{display['reasoning']}</p>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
                if display["wait_info"]:
                    st.info(f"⏳ {display['wait_info']}")

                if rec.sources:
                    st.markdown("**Fuentes:**")
                    for source in rec.sources[:3]:
                        st.markdown(f"- [{source[:60]}...]({source})")

                # Snap del precio
                snap = ctx.market.get(rec.ticker)
                if snap:
                    st.caption(
                        f"Precio actual: ${snap.current_price:.2f} ({snap.change_pct:+.2f}%)"
                    )

            with col2:
                render_mini_chart(rec.ticker)


if __name__ == "__main__":
    main()
