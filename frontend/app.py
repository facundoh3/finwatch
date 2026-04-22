"""
finwatch — Asistente personal de finanzas
Ejecutar con: bash run.sh
"""
import asyncio
import sys
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


def _load_tickers_config() -> dict:
    if _TICKERS_PATH.exists():
        return yaml.safe_load(_TICKERS_PATH.read_text())
    return {}


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

    # Categorías USA
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
    st.sidebar.caption("Cache: 30 min · Noticias: 24hs")
    st.sidebar.caption("Qwen3.6 (contexto) · Claude (análisis)")

    tickers_usa = tickers_etf + tickers_acciones
    return tickers_usa, tickers_byma, force_refresh


def _check_settings():
    from config.settings import get_settings
    s = get_settings()
    issues = []
    if not s.anthropic_api_key:
        issues.append("❌ **ANTHROPIC_API_KEY** no configurada — sin análisis Claude")
    if not s.openrouter_api_key:
        issues.append("⚠️ **OPENROUTER_API_KEY** no configurada — sin filtrado Qwen")
    if not s.finnhub_api_key:
        issues.append("⚠️ **FINNHUB_API_KEY** no configurada — datos limitados")
    return issues


def main():
    cfg = _load_tickers_config()
    tickers_usa, tickers_byma, force_refresh = _sidebar(cfg)

    st.title("📈 finwatch")
    st.caption("Tu asistente personal de finanzas — mercados USA y Argentina")

    # Mostrar advertencias de configuración
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

    if force_refresh or "analysis_result" not in st.session_state:
        with st.spinner("Analizando mercados... (~30 segundos)"):
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
                return

    ctx, recs = st.session_state["analysis_result"]

    # Resumen del mercado
    if recs.market_summary:
        st.info(f"**Panorama del mercado hoy:** {recs.market_summary}")

    # Métricas rápidas
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("✅ Comprar", len(recs.by_action("BUY")))
    col2.metric("⏳ Esperar", len(recs.by_action("WAIT")))
    col3.metric("❌ Evitar", len(recs.by_action("AVOID")))
    col4.metric("📰 Noticias", len(ctx.news.items))

    st.divider()

    tab1, tab2, tab3 = st.tabs(["💡 Recomendaciones", "📊 Precios", "📰 Noticias"])

    with tab1:
        _render_recomendaciones(recs, ctx)
    with tab2:
        _render_precios(ctx)
    with tab3:
        _render_noticias(ctx)


def _show_welcome():
    st.markdown("""
    ### ¿Qué hace finwatch?
    - 📊 Monitorea índices, commodities, sectores y acciones en USA y Argentina
    - 📰 Analiza noticias financieras con IA y explica su impacto
    - 💡 Te dice qué **comprar, esperar o evitar** hoy
    - 🔍 Cubre oro, petróleo, real estate, tecnología, energía y más
    """)


def _render_recomendaciones(recs, ctx):
    if not recs.recommendations:
        st.warning("No hay recomendaciones. Verificá las API keys y volvé a analizar.")
        return

    order = {"BUY": 0, "WAIT": 1, "AVOID": 2}
    sorted_recs = sorted(recs.recommendations, key=lambda r: order.get(r.action.value, 3))

    for rec in sorted_recs:
        display = rec.to_display_dict()
        colors = {"BUY": "#1a4a1a", "WAIT": "#4a3a00", "AVOID": "#4a1010"}
        bg = colors.get(rec.action.value, "#222")

        snap = ctx.market.get(rec.ticker)
        price_info = f" · ${snap.current_price:.2f} ({snap.change_pct:+.1f}%)" if snap else ""

        with st.expander(
            f"{display['action_label']} **{rec.ticker}**{price_info} — {display['confidence_label']}",
            expanded=(rec.action.value == "BUY"),
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


def _render_precios(ctx):
    if not ctx.market.snapshots:
        st.info("No hay datos de precio disponibles.")
        return

    from core.models.market import PriceDirection
    rows = []
    for s in sorted(ctx.market.snapshots, key=lambda x: abs(x.change_pct), reverse=True):
        arrow = "▲" if s.direction == PriceDirection.UP else ("▼" if s.direction == PriceDirection.DOWN else "▶")
        color = "🟢" if s.direction == PriceDirection.UP else ("🔴" if s.direction == PriceDirection.DOWN else "⚪")
        rows.append({
            "": color,
            "Ticker": s.ticker,
            "Precio": f"${s.current_price:.2f}",
            "Cambio %": f"{arrow} {s.change_pct:+.2f}%",
            "Volumen": f"{s.volume/1_000_000:.1f}M" if s.volume >= 1_000_000 else str(s.volume),
        })

    st.dataframe(rows, use_container_width=True, hide_index=True)


def _render_noticias(ctx):
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

    if not items:
        st.info("No hay noticias con los filtros seleccionados.")
        return

    for news in items[:30]:
        render_news_card(news)


if __name__ == "__main__":
    main()
