import streamlit as st
import streamlit.components.v1 as components


def render_chart(ticker: str, theme: str = "dark", height: int = 400) -> None:
    """
    Embebe un widget de TradingView para el ticker dado.
    No requiere API key — usa el embed público gratuito de TradingView.
    """
    widget_html = f"""
    <div class="tradingview-widget-container" style="height:{height}px;">
      <div id="tradingview_{ticker}"></div>
      <script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
      <script type="text/javascript">
        new TradingView.widget({{
          "autosize": true,
          "symbol": "{ticker}",
          "interval": "D",
          "timezone": "America/Argentina/Buenos_Aires",
          "theme": "{theme}",
          "style": "1",
          "locale": "es",
          "toolbar_bg": "#f1f3f6",
          "enable_publishing": false,
          "allow_symbol_change": true,
          "container_id": "tradingview_{ticker}",
          "height": {height}
        }});
      </script>
    </div>
    """
    components.html(widget_html, height=height + 20, scrolling=False)


def render_mini_chart(ticker: str, theme: str = "dark") -> None:
    """Widget compacto de mini-chart para usar en cards."""
    widget_html = f"""
    <div class="tradingview-widget-container">
      <div class="tradingview-widget-container__widget"></div>
      <script type="text/javascript" src="https://s3.tradingview.com/external-embedding/embed-widget-mini-symbol-overview.js" async>
      {{
        "symbol": "{ticker}",
        "width": "100%",
        "height": 220,
        "locale": "es",
        "dateRange": "1M",
        "colorTheme": "{theme}",
        "isTransparent": false,
        "autosize": true,
        "largeChartUrl": ""
      }}
      </script>
    </div>
    """
    components.html(widget_html, height=240, scrolling=False)


def render_market_overview(theme: str = "dark") -> None:
    """Widget de overview del mercado (índices principales)."""
    widget_html = f"""
    <div class="tradingview-widget-container">
      <div class="tradingview-widget-container__widget"></div>
      <script type="text/javascript" src="https://s3.tradingview.com/external-embedding/embed-widget-market-overview.js" async>
      {{
        "colorTheme": "{theme}",
        "dateRange": "1D",
        "showChart": true,
        "locale": "es",
        "largeChartUrl": "",
        "isTransparent": false,
        "showSymbolLogo": true,
        "showFloatingTooltip": false,
        "width": "100%",
        "height": 500,
        "tabs": [
          {{
            "title": "Índices",
            "symbols": [
              {{"s": "FOREXCOM:SPXUSD", "d": "S&P 500"}},
              {{"s": "FOREXCOM:NSXUSD", "d": "Nasdaq 100"}},
              {{"s": "INDEX:MERVAL", "d": "Merval"}},
              {{"s": "FOREXCOM:DJI", "d": "Dow Jones"}}
            ]
          }},
          {{
            "title": "ARG ADRs",
            "symbols": [
              {{"s": "NYSE:YPF", "d": "YPF"}},
              {{"s": "NASDAQ:GGAL", "d": "Galicia"}},
              {{"s": "NYSE:BMA", "d": "Banco Macro"}},
              {{"s": "NYSE:BBAR", "d": "BBVA ARG"}}
            ]
          }}
        ]
      }}
      </script>
    </div>
    """
    components.html(widget_html, height=520, scrolling=False)
