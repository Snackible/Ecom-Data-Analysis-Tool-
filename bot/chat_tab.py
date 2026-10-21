"""Streamlit chat tab for Snack Diva (the Ads Bot). Fully local: no external API or LLM."""
import streamlit as st

from . import answers, engine, settings as settings_mod

HISTORY_KEY = "ads_bot_history"
PREV_KEY = "ads_bot_prev"
PENDING_KEY = "ads_bot_pending"
EXAMPLES = [
    "Which city is consuming more budget?",
    "Which search queries spent the most with ROAS below 1?",
    "What is my attack, defend and expand split?",
    "How did spend and ROAS change versus last week?",
    "What changes should I make this week?",
    "Which campaigns deserve more budget?",
]


def _unset_settings(s: dict) -> list:
    out = []
    for k, v in s.items():
        if isinstance(v, dict):
            out += [f"{k}.{kk}" for kk, vv in v.items() if vv in (None, [], {})]
        elif v in (None, [], {}):
            out.append(k)
    return out


def _chips(items, prefix):
    """Clickable question buttons. A click stores the text and reruns, and the
    main loop treats it like a typed message."""
    items = [i for i in items if i]
    if not items:
        return
    cols = st.columns(min(len(items), 3))
    for i, text in enumerate(items):
        if cols[i % len(cols)].button(text, key=f"{prefix}_{i}", use_container_width=True):
            st.session_state[PENDING_KEY] = text
            st.rerun()


def _show(msg: dict, idx: int = 0, latest: bool = False):
    s = msg.get("struct")
    if not s:
        st.markdown(msg.get("content", ""))
        return
    if s["date"]:
        st.caption(f"📅 {s['date']}")
    if s["head"]:
        st.markdown(f"##### {s['head']}")
    if s["lead"]:
        st.markdown(s["lead"])
    if s["extra"]:
        st.markdown(s["extra"])
    if s["deep"]:
        st.markdown("**Deep dive**")
        tabs = st.tabs([label for label, _, _ in s["deep"]])
        for tab, (_, title, body) in zip(tabs, s["deep"]):
            with tab:
                st.markdown(f"**{title}**")
                st.markdown(body)
    if s["notes"]:
        st.markdown(s["notes"])
    if s["tables"]:
        with st.expander("Supporting numbers"):
            st.markdown(s["tables"])
    if latest and s["suggestions"]:
        st.caption("Ask next")
        _chips(s["suggestions"], f"sug{idx}")


def render():
    st.subheader("💅 Snack Diva")
    st.caption("Ask in plain English. Answers are written from the loaded Instamart ads data and computed locally "
               "(no external AI service). Every answer is a deep dive: what, why, how, whom, similarity, cause and what to do next. "
               "You can follow up, for example: \"and for Mumbai?\" or \"top 20 instead\".")
    if HISTORY_KEY not in st.session_state:
        st.session_state[HISTORY_KEY] = []
    hist = st.session_state[HISTORY_KEY]

    with st.expander("What can I ask? / settings status"):
        st.markdown("Any question about spend, impressions, clicks, GMV, ROAS, CPM, CPC, cities, SKUs, keywords, search queries, "
                    "placements or dates works, including trends, comparisons and row-level lookups. The full list is in `bot/questions.md`.")
        unset = _unset_settings(settings_mod.load())
        if unset:
            st.markdown(f"{len(unset)} settings in `bot/settings.yaml` are still empty. Answers that need them say `needs setting: <name>`.")
    if hist and st.button("Clear chat"):
        st.session_state[HISTORY_KEY] = []
        st.session_state[PREV_KEY] = None
        st.rerun()

    if not hist:
        st.markdown("**Try one of these**")
        _chips(EXAMPLES, "ex")

    last_assistant = max((i for i, m in enumerate(hist) if m["role"] == "assistant"), default=-1)
    for i, m in enumerate(hist):
        with st.chat_message(m["role"]):
            _show(m, i, latest=(i == last_assistant))

    prompt = st.chat_input("Ask Snack Diva about the Instamart ads data") or st.session_state.pop(PENDING_KEY, None)
    if prompt:
        hist.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)
        with st.chat_message("assistant"):
            with st.spinner("Working it out from the data..."):
                try:
                    res = engine.ask(prompt, prev=st.session_state.get(PREV_KEY))
                    struct = answers.render_struct(res)
                    if res.meta:
                        st.session_state[PREV_KEY] = res.meta
                    msg = {"role": "assistant", "struct": struct}
                except Exception:
                    msg = {"role": "assistant", "content": "Something went wrong answering that. Try rephrasing it."}
            hist.append(msg)
        st.rerun()  # redraw so only the newest answer carries the follow-up buttons
