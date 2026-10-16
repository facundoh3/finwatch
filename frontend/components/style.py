"""Estilos globales. Solo colores semitransparentes, así se ven bien en tema claro y oscuro."""
import streamlit as st

_CSS = """
<style>
.block-container { padding-top: 3rem; padding-bottom: 3rem; max-width: 1100px; }
[data-testid="stMetric"] {
  background: rgba(127, 127, 127, 0.07);
  border: 1px solid rgba(127, 127, 127, 0.18);
  border-radius: 14px;
  padding: 12px 16px;
}
[data-testid="stMetricLabel"] p { font-size: 0.85rem; opacity: 0.8; }
button[data-baseweb="tab"] p { font-size: 1rem; }
@media (max-width: 640px) {
  .block-container { padding: 3.5rem 1rem 2rem 1rem; }
  h2 { font-size: 1.5rem !important; }
}
</style>
"""


def inject_css() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)
