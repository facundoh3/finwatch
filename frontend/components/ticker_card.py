import streamlit as st

from core.models.market import MarketSnapshot, PriceDirection
from core.models.recommendation import Recommendation


def render_ticker_card(
    snapshot: MarketSnapshot,
    recommendation: Recommendation | None = None,
) -> None:
    """Card con precio + dirección + recomendación para un ticker."""
    direction_icon = {
        PriceDirection.UP: "▲",
        PriceDirection.DOWN: "▼",
        PriceDirection.FLAT: "▶",
    }.get(snapshot.direction, "▶")

    direction_color = {
        PriceDirection.UP: "green",
        PriceDirection.DOWN: "red",
        PriceDirection.FLAT: "gray",
    }.get(snapshot.direction, "gray")

    with st.container(border=True):
        col1, col2 = st.columns([2, 1])
        with col1:
            st.markdown(f"### {snapshot.ticker}")
            st.markdown(
                f"**${snapshot.current_price:.2f}** "
                f"<span style='color:{direction_color}'>"
                f"{direction_icon} {snapshot.change_pct:+.2f}%</span>",
                unsafe_allow_html=True,
            )
            if snapshot.high_52w and snapshot.low_52w:
                st.caption(f"52w: ${snapshot.low_52w:.2f} — ${snapshot.high_52w:.2f}")

        with col2:
            if recommendation:
                _render_action_badge(recommendation)


def _render_action_badge(rec: Recommendation) -> None:
    display = rec.to_display_dict()
    action_colors = {"BUY": "#1a7a1a", "WAIT": "#8a6a00", "AVOID": "#8a1a1a"}
    color = action_colors.get(rec.action.value, "#333")
    st.markdown(
        f"<div style='background:{color};padding:8px;border-radius:8px;text-align:center'>"
        f"<b>{display['action_label']}</b><br>"
        f"<small style='color:{display['confidence_color']}'>{display['confidence_label']}</small>"
        f"</div>",
        unsafe_allow_html=True,
    )
    if display["wait_info"]:
        st.caption(display["wait_info"])
