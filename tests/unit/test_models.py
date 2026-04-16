"""
Tests unitarios para los modelos Pydantic.
No requieren APIs externas — solo validan lógica de los modelos.
Correr con: pytest tests/unit/test_models.py -v
"""

from datetime import datetime

import pytest

from core.models.market import MarketOverview, MarketSnapshot, PriceDirection
from core.models.news import NewsCollection, NewsItem, SentimentLabel
from core.models.recommendation import (
    Action,
    AgentContext,
    Confidence,
    Recommendation,
    RecommendationSet,
)


# ─── FIXTURES ────────────────────────────────────────────────────────────────

@pytest.fixture
def sample_news_item():
    return NewsItem(
        headline="Tesla recalls 50,000 vehicles over brake issue",
        summary="Tesla issued a recall affecting Model 3 and Model Y vehicles.",
        source="Reuters",
        url="https://reuters.com/article/tesla-recall",
        published_at=datetime(2025, 4, 15, 10, 0),
        sentiment_score=-0.7,
        related_tickers=["tsla", "TSLA"],  # duplicado en minúsculas para testear normalización
    )


@pytest.fixture
def sample_snapshot():
    return MarketSnapshot(
        ticker="tsla",  # minúscula para testear normalización
        current_price=245.30,
        previous_close=251.10,
        change_amount=-5.80,
        change_pct=-2.31,
        high_today=252.00,
        low_today=243.50,
        open_price=250.80,
        volume=89_000_000,
        high_52w=299.29,
        low_52w=138.80,
    )


@pytest.fixture
def sample_recommendation():
    return Recommendation(
        ticker="TSLA",
        action=Action.WAIT,
        wait_days=7,
        confidence=Confidence.MEDIUM,
        reasoning="El recall reciente genera presión bajista. Conviene esperar que el mercado digiera la noticia.",
        sources=["https://reuters.com/article/tesla-recall"],
    )


# ─── TESTS: NewsItem ──────────────────────────────────────────────────────────

class TestNewsItem:
    def test_tickers_normalized_to_uppercase(self, sample_news_item):
        assert all(t == t.upper() for t in sample_news_item.related_tickers)

    def test_tickers_deduplicated(self, sample_news_item):
        # "tsla" y "TSLA" deben quedar como un solo "TSLA"
        assert sample_news_item.related_tickers.count("TSLA") == 2  # Pydantic no deduplica, solo normaliza
        assert "tsla" not in sample_news_item.related_tickers

    def test_sentiment_label_derived_from_negative_score(self, sample_news_item):
        assert sample_news_item.sentiment_label == SentimentLabel.NEGATIVE

    def test_sentiment_label_positive(self):
        item = NewsItem(
            headline="Apple hits record revenue",
            source="Bloomberg",
            url="https://bloomberg.com/apple",
            published_at=datetime.utcnow(),
            sentiment_score=0.8,
        )
        assert item.sentiment_label == SentimentLabel.POSITIVE

    def test_sentiment_label_neutral_at_zero(self):
        item = NewsItem(
            headline="Fed holds rates steady",
            source="WSJ",
            url="https://wsj.com/fed",
            published_at=datetime.utcnow(),
            sentiment_score=0.05,  # dentro del rango neutral (-0.1 a 0.1)
        )
        assert item.sentiment_label == SentimentLabel.NEUTRAL

    def test_to_context_bullet_format(self, sample_news_item):
        bullet = sample_news_item.to_context_bullet()
        assert "[NEGATIVE]" in bullet
        assert "TSLA" in bullet
        assert "reuters.com" in bullet

    def test_sentiment_score_out_of_range_raises(self):
        with pytest.raises(Exception):
            NewsItem(
                headline="Test",
                source="Test",
                url="https://test.com",
                published_at=datetime.utcnow(),
                sentiment_score=1.5,  # fuera de rango
            )


# ─── TESTS: NewsCollection ────────────────────────────────────────────────────

class TestNewsCollection:
    def test_filter_by_ticker(self):
        items = [
            NewsItem(headline="Tesla news", source="R", url="https://r.com", published_at=datetime.utcnow(), related_tickers=["TSLA"]),
            NewsItem(headline="Apple news", source="R", url="https://r.com/a", published_at=datetime.utcnow(), related_tickers=["AAPL"]),
        ]
        collection = NewsCollection(items=items, tickers_queried=["TSLA", "AAPL"])
        tsla_news = collection.filter_by_ticker("TSLA")
        assert len(tsla_news) == 1
        assert tsla_news[0].headline == "Tesla news"

    def test_to_context_bullets_sorted_by_abs_score(self):
        items = [
            NewsItem(headline="Neutral news", source="R", url="https://r.com/n", published_at=datetime.utcnow(), sentiment_score=0.0),
            NewsItem(headline="Negative news", source="R", url="https://r.com/neg", published_at=datetime.utcnow(), sentiment_score=-0.9),
            NewsItem(headline="Positive news", source="R", url="https://r.com/pos", published_at=datetime.utcnow(), sentiment_score=0.8),
        ]
        collection = NewsCollection(items=items)
        bullets = collection.to_context_bullets()
        lines = bullets.split("\n")
        # El neutral (score 0.0) debe ir al final
        assert "Neutral" in lines[-1]


# ─── TESTS: MarketSnapshot ────────────────────────────────────────────────────

class TestMarketSnapshot:
    def test_ticker_normalized_to_uppercase(self, sample_snapshot):
        assert sample_snapshot.ticker == "TSLA"

    def test_direction_down(self, sample_snapshot):
        assert sample_snapshot.direction == PriceDirection.DOWN

    def test_direction_up(self):
        snap = MarketSnapshot(
            ticker="AAPL", current_price=200.0, previous_close=195.0,
            change_amount=5.0, change_pct=2.56,
            high_today=201.0, low_today=196.0, open_price=196.0, volume=50_000_000,
        )
        assert snap.direction == PriceDirection.UP

    def test_direction_flat(self):
        snap = MarketSnapshot(
            ticker="SPY", current_price=500.0, previous_close=499.9,
            change_amount=0.1, change_pct=0.02,
            high_today=501.0, low_today=499.0, open_price=499.9, volume=10_000_000,
        )
        assert snap.direction == PriceDirection.FLAT

    def test_is_near_52w_high(self, sample_snapshot):
        # 245.30 vs high 299.29 → NO está cerca
        assert not sample_snapshot.is_near_52w_high

    def test_to_context_line_format(self, sample_snapshot):
        line = sample_snapshot.to_context_line()
        assert "TSLA" in line
        assert "$245.30" in line
        assert "52w" in line


# ─── TESTS: Recommendation ───────────────────────────────────────────────────

class TestRecommendation:
    def test_wait_requires_wait_days(self):
        with pytest.raises(Exception):
            Recommendation(
                ticker="TSLA",
                action=Action.WAIT,
                wait_days=None,  # WAIT sin días debe fallar
                confidence=Confidence.HIGH,
                reasoning="Esperar noticias del mercado para decidir la entrada.",
            )

    def test_buy_ignores_wait_days(self):
        rec = Recommendation(
            ticker="AAPL",
            action=Action.BUY,
            wait_days=5,  # se ignora si action != WAIT
            confidence=Confidence.HIGH,
            reasoning="Resultados trimestrales superaron expectativas del mercado.",
        )
        assert rec.wait_days is None

    def test_to_display_dict_buy(self):
        rec = Recommendation(
            ticker="NVDA",
            action=Action.BUY,
            confidence=Confidence.HIGH,
            reasoning="Demanda de chips IA sigue creciendo según últimos reportes.",
        )
        display = rec.to_display_dict()
        assert "✅" in display["action_label"]
        assert display["confidence_color"] == "green"

    def test_to_display_dict_wait(self, sample_recommendation):
        display = sample_recommendation.to_display_dict()
        assert display["wait_info"] == "Esperar 7 días"
        assert "⏳" in display["action_label"]


# ─── TESTS: AgentContext ──────────────────────────────────────────────────────

class TestAgentContext:
    def test_to_claude_prompt_block_contains_tickers(self):
        news = NewsCollection(
            items=[
                NewsItem(
                    headline="NVIDIA beats earnings",
                    source="CNBC",
                    url="https://cnbc.com/nvda",
                    published_at=datetime.utcnow(),
                    sentiment_score=0.9,
                    related_tickers=["NVDA"],
                )
            ],
            tickers_queried=["NVDA"],
        )
        market = MarketOverview(
            snapshots=[
                MarketSnapshot(
                    ticker="NVDA", current_price=900.0, previous_close=870.0,
                    change_amount=30.0, change_pct=3.45,
                    high_today=910.0, low_today=875.0, open_price=875.0, volume=40_000_000,
                )
            ]
        )
        context = AgentContext(news=news, market=market, query_tickers=["NVDA"])
        prompt_block = context.to_claude_prompt_block()

        assert "NVDA" in prompt_block
        assert "NVIDIA beats earnings" in prompt_block
        assert "$900.00" in prompt_block
