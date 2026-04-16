import streamlit as st

from core.models.news import NewsItem, SentimentLabel


def render_news_card(news: NewsItem) -> None:
    """Card de noticia con sentimiento, impacto y fuente."""
    sentiment_color = {
        SentimentLabel.POSITIVE: "#1a7a1a",
        SentimentLabel.NEGATIVE: "#8a1a1a",
        SentimentLabel.NEUTRAL: "#555",
    }.get(news.sentiment_label, "#555")

    sentiment_label = {
        SentimentLabel.POSITIVE: "POSITIVO",
        SentimentLabel.NEGATIVE: "NEGATIVO",
        SentimentLabel.NEUTRAL: "NEUTRAL",
    }.get(news.sentiment_label, "NEUTRAL")

    tier_badge = {
        "A": "🟢 Fuente verificada",
        "B": "🟡 Fuente no verificada",
        "C": "🔴 Fuente desconocida",
    }.get(news.source_tier, "🔴")

    unverified_warning = (
        " ⚠️ *Fuente única*" if news.corroborated_by == 1 and news.source_tier != "A" else ""
    )

    with st.container(border=True):
        col1, col2 = st.columns([4, 1])
        with col1:
            st.markdown(f"**{news.headline}**")
            if news.summary:
                st.caption(news.summary[:150] + "..." if len(news.summary) > 150 else news.summary)
        with col2:
            st.markdown(
                f"<span style='color:{sentiment_color};font-weight:bold'>{sentiment_label}</span>",
                unsafe_allow_html=True,
            )

        col_a, col_b, col_c = st.columns([2, 2, 2])
        with col_a:
            st.caption(f"📰 {news.source}")
        with col_b:
            st.caption(tier_badge + unverified_warning)
        with col_c:
            if news.url:
                st.markdown(f"[Ver noticia →]({news.url})")

        if news.impact_explanation:
            st.info(f"💡 **Impacto**: {news.impact_explanation}")

        if news.related_tickers:
            tickers_str = " ".join(f"`{t}`" for t in news.related_tickers[:5])
            st.markdown(f"Tickers: {tickers_str}")
