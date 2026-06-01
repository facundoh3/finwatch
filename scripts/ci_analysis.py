"""
Script para GitHub Actions: corre el análisis diario y guarda en config/ci_analysis.json.
El resultado se commitea al repo para que esté disponible cuando el usuario abre la app.

Uso local: python scripts/ci_analysis.py
"""
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

# Asegura que el proyecto esté en el path
sys.path.insert(0, str(Path(__file__).parent.parent))

OUTPUT_FILE = Path(__file__).parent.parent / "config" / "ci_analysis.json"


def _load_tickers() -> tuple[list[str], list[str]]:
    import yaml
    tickers_path = Path(__file__).parent.parent / "config" / "tickers.yaml"
    if not tickers_path.exists():
        return ["SPY", "QQQ", "GLD", "AAPL", "NVDA", "TSLA"], ["YPFD", "GGAL"]
    data = yaml.safe_load(tickers_path.read_text())
    tickers_usa = (
        data.get("indices_usa", [])[:3] +
        data.get("commodities", [])[:2] +
        data.get("acciones_usa", [])[:5]
    )
    tickers_byma = data.get("tickers_byma", [])[:4]
    return tickers_usa, tickers_byma


async def run():
    from agents.orchestrator import analyze
    from core.services.tracker_service import resolve_pending, save_recommendations

    tickers_usa, tickers_byma = _load_tickers()
    print(f"[CI] Analizando USA={tickers_usa} | BYMA={tickers_byma}")

    ctx, recs = await analyze(tickers_usa=tickers_usa, tickers_byma=tickers_byma)

    # Resolver tracker + guardar nuevas recomendaciones
    current_prices = {s.ticker: s.current_price for s in ctx.market.snapshots}
    resolve_pending(current_prices)
    save_recommendations(recs.recommendations, ctx.market.snapshots)

    # Guardar resultado para que la app lo levante al iniciar
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps({
        "saved_at": datetime.now().isoformat(),
        "source": "github_actions",
        "ctx": ctx.model_dump(mode="json"),
        "recs": recs.model_dump(mode="json"),
    }, ensure_ascii=False, default=str))

    print(f"[CI] {len(recs.recommendations)} recomendaciones guardadas en {OUTPUT_FILE}")
    if recs.market_summary:
        print(f"[CI] Resumen: {recs.market_summary[:120]}")


if __name__ == "__main__":
    asyncio.run(run())
