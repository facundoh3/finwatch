import streamlit as st

from core.models.news import NewsItem, SentimentLabel


def _esc(text: str) -> str:
    return (text or "").replace("$", "\\$")


def render_news_card(news: NewsItem) -> None:
    """Card de noticia con sentimiento, impacto y fuente."""
    sentiment_color = {
        SentimentLabel.POSITIVE: "green",
        SentimentLabel.NEGATIVE: "red",
        SentimentLabel.NEUTRAL: "gray",
    }.get(news.sentiment_label, "gray")

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
            st.markdown(f"**{_esc(news.headline)}**")
            if news.summary:
                st.caption(_esc(news.summary[:150] + "..." if len(news.summary) > 150 else news.summary))
        with col2:
            st.markdown(f":{sentiment_color}[**{sentiment_label}**]")

        col_a, col_b, col_c = st.columns([2, 2, 2])
        with col_a:
            st.caption(f"📰 {news.source}")
        with col_b:
            st.caption(tier_badge + unverified_warning)
        with col_c:
            if news.url:
                st.markdown(f"[Ver noticia →]({news.url})")

        if news.impact_explanation:
            st.info(_esc(f"💡 **Impacto**: {news.impact_explanation}"))

        if news.related_tickers:
            tickers_str = " ".join(f"`{t}`" for t in news.related_tickers[:5])
            st.markdown(f"Tickers: {tickers_str}")
