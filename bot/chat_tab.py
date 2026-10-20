"""Streamlit chat tab for the Ads Bot. Fully local: no external API or LLM."""
import streamlit as st

from . import answers, engine, settings as settings_mod

HISTORY_KEY = "ads_bot_history"
EXAMPLES = [
    "Which city is consuming more budget?",
    "Which search queries spent the most with ROAS below 1?",
    "What is my attack, defend and expand split?",
    "How did spend and ROAS change versus last week?",
    "What changes should I make this week?",
]


def _unset_settings(s: dict) -> list:
    out = []
    for k, v in s.items():
        if isinstance(v, dict):
            out += [f"{k}.{kk}" for kk, vv in v.items() if vv in (None, [], {})]
        elif v in (None, [], {}):
            out.append(k)
    return out


def render():
    st.subheader("Ads Bot")
    st.caption("Ask in plain English. Answers come from the loaded Instamart ads data only and are computed locally "
               "(no external AI service). Text and small tables, no charts.")
    if HISTORY_KEY not in st.session_state:
        st.session_state[HISTORY_KEY] = []
    hist = st.session_state[HISTORY_KEY]

    with st.expander("What can I ask? / settings status"):
        st.markdown("Examples:\n" + "\n".join(f"- {e}" for e in EXAMPLES))
        st.markdown("Any question about spend, impressions, clicks, GMV, ROAS, CPM, CPC, cities, SKUs, keywords, search queries, "
                    "placements or dates works, including trends, comparisons and row-level lookups. The full list is in `bot/questions.md`.")
        unset = _unset_settings(settings_mod.load())
        if unset:
            st.markdown(f"{len(unset)} settings in `bot/settings.yaml` are still empty. Answers that need them say `needs setting: <name>`.")
    if hist and st.button("Clear chat"):
        st.session_state[HISTORY_KEY] = []
        st.rerun()

    for m in hist:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])

    prompt = st.chat_input("Ask about the Instamart ads data")
    if prompt:
        hist.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)
        with st.chat_message("assistant"):
            with st.spinner("Working it out from the data..."):
                try:
                    md = answers.render(engine.ask(prompt))
                except Exception:
                    md = "Something went wrong answering that. Try rephrasing it."
            st.markdown(md)
        hist.append({"role": "assistant", "content": md})
