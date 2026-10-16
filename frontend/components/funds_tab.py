"""Pestaña 🏦 Grandes fondos — qué tienen y qué movieron, según su 13F en la SEC."""
import asyncio
from pathlib import Path

import streamlit as st

from core.services.cache_service import CacheService
from core.services.institutional_service import (
    BERKSHIRE_CIK,
    STATUS_ADDED,
    STATUS_NEW,
    STATUS_SAME,
    STATUS_SOLD,
    STATUS_TRIMMED,
    get_fund_holdings,
    load_filers,
)

_STATUS_ICON = {STATUS_NEW: "🆕", STATUS_ADDED: "🟢", STATUS_TRIMMED: "🟠", STATUS_SAME: "⚪", STATUS_SOLD: "🔴"}


@st.cache_data(ttl=600, show_spinner=False)
def _load_fund(cik: str, user_agent: str) -> tuple:
    """(fondo, error). El error también se cachea 10 minutos: reintentar enseguida alarga un bloqueo de la SEC."""
    from core.services.fundamentals_service import SECBlockedError

    try:
        return asyncio.run(get_fund_holdings(cik, user_agent, CacheService(cache_dir=Path("data/cache")))), None
    except SECBlockedError as e:
        return None, str(e)
    except Exception as e:
        return None, f"No se pudo leer el 13F de la SEC: {e}"


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def _cedears(tickers: tuple[str, ...]) -> dict[str, bool] | None:
    """Qué tickers se pueden comprar en BYMA como CEDEAR (consulta IOL). None = sin IOL."""
    from config.settings import get_settings
    from core.services.iol_client import IOLClient

    s = get_settings()
    if not (s.iol_username and s.iol_password):
        return None

    async def check() -> dict[str, bool]:
        iol = IOLClient(s.iol_username, s.iol_password)
        return {t: (await iol.get_quote(t.replace(".", ""), market="bCBA")) is not None for t in tickers}

    try:
        return asyncio.run(check())
    except Exception:
        return None


def _movement(h) -> str:
    icon = _STATUS_ICON.get(h.status, "")
    if h.status in (STATUS_ADDED, STATUS_TRIMMED) and h.change_pct is not None:
        return f"{icon} {h.status} ({h.change_pct:+.0f}% acciones)"
    return f"{icon} {h.status}"


def render_funds_tab() -> None:
    from config.settings import get_settings
    from core.services.fundamentals_service import valid_user_agent

    st.subheader("🏦 ¿Qué compran los grandes fondos?")
    ua = get_settings().sec_user_agent
    if not valid_user_agent(ua):
        st.info(
            "Para ver esto, agregá a tu `.env` la línea `SEC_USER_AGENT=Tu Nombre tu@email.com` y reiniciá la app. "
            "La SEC (el regulador de EE.UU.) pide nombre y email para usar sus datos públicos; es gratis."
        )
        return

    filers = [f for f in load_filers() if "status" not in f]
    labels = {f["cik"]: f"{f['name']} — {f['person']}" for f in filers}
    ciks = list(labels)
    cik = st.selectbox(
        "Fondo", ciks, index=ciks.index(BERKSHIRE_CIK) if BERKSHIRE_CIK in ciks else 0,
        format_func=labels.get,
    )

    with st.spinner("Leyendo el último reporte 13F en la SEC..."):
        fund, error = _load_fund(cik, ua)
    if error:
        st.warning(error)
        if st.button("🔄 Reintentar", key="funds_retry"):
            _load_fund.clear()
            st.rerun()
        return
    if fund is None:
        st.warning("No se encontraron datos para este fondo.")
        return

    st.caption(
        f"Tenencias al **{fund.period}**, presentado el {fund.filed} · total declarado "
        f"US\\${fund.total_value / 1e9:,.1f} mil millones"
        + (f" · comparado con {fund.prior_period}" if fund.prior_period else "")
        + f" · [ver en la SEC]({fund.url})"
    )
    st.warning(
        "⏳ Estos datos tienen entre 45 días y 4 meses y medio de atraso: muestran lo que el fondo tenía al cierre "
        "del trimestre, no lo que tiene hoy. Sirven para entender qué piensan los grandes, no para copiarlos a ciegas."
    )

    counts = {s: sum(h.status == s for h in fund.holdings) for s in (STATUS_NEW, STATUS_ADDED, STATUS_TRIMMED)}
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("🆕 Compras nuevas", counts[STATUS_NEW])
    c2.metric("🟢 Aumentó", counts[STATUS_ADDED])
    c3.metric("🟠 Redujo", counts[STATUS_TRIMMED])
    c4.metric("🔴 Vendió todo", len(fund.sold))

    n = len(fund.holdings)
    top = st.slider("Cuántas posiciones mostrar", 5, min(50, n), min(15, n)) if n > 5 else n
    shown = fund.holdings[:top]
    cedears = _cedears(tuple(h.ticker for h in shown + fund.sold if h.ticker))

    def row(h) -> dict:
        r = {
            "Empresa": h.name,
            "Ticker": (h.ticker or "—") + (" (?)" if h.ticker and h.ticker_check else ""),
            "% del fondo": f"{h.pct:.2f}%" if h.status != STATUS_SOLD else "—",
            "Movimiento": _movement(h),
        }
        if cedears is not None:
            r["CEDEAR en BYMA"] = "✅" if h.ticker and cedears.get(h.ticker) else "—"
        return r

    st.dataframe([row(h) for h in shown], hide_index=True, width="stretch")
    if fund.sold:
        st.markdown("**Vendió todo este trimestre**")
        st.dataframe([row(h) for h in fund.sold], hide_index=True, width="stretch")

    notes = ["(?) = el ticker se dedujo por el nombre y conviene verificarlo."]
    if cedears is None:
        notes.append("Configurá IOL para ver cuáles se pueden comprar como CEDEAR.")
    else:
        notes.append("CEDEAR ✅ = se puede comprar en pesos en BYMA a través de IOL.")
    st.caption(" ".join(notes))

    with st.expander("¿Qué es un 13F?"):
        st.markdown(
            "Los fondos que manejan más de US\\$100 millones en acciones de EE.UU. tienen que informar a la SEC, "
            "cada trimestre, qué acciones tienen. Ese informe es el **13F**.\n\n"
            "- Muestra solo acciones de EE.UU. que el fondo **tiene** (no las ventas en corto, ni bonos, ni efectivo).\n"
            "- Se presenta hasta **45 días después** de terminado el trimestre.\n"
            "- No muestra lo que compraron y vendieron dentro del mismo trimestre.\n"
            "- Lo presenta la empresa, no la persona: Berkshire Hathaway, no Warren Buffett."
        )
