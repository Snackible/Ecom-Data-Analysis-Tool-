"""Streamlit chat tab for Snackibella (the Ads Bot). Fully local: no external API or LLM."""
import streamlit as st

from . import alerts, answers, engine, multi, settings as settings_mod, sku_learn
from .core import Context

HISTORY_KEY = "ads_bot_history"
PREV_KEY = "ads_bot_prev"
PENDING_KEY = "ads_bot_pending"
ALERT_KEY = "ads_bot_alerts_seen"
EXAMPLES = [
    "Which city is consuming more budget?",
    "Which search queries spent the most with ROAS below 1?",
    "What is my attack, defend and expand split?",
    "How did spend and ROAS change versus last week?",
    "What changes should I make this week?",
    "Which campaigns deserve more budget?",
]
WEEKLY_CHECKUP = (
    "Give me a one-line verdict on Instamart ads for this period; "
    "and how did spend and ROAS change versus last week; "
    "and which 3 campaigns are the biggest money burners; "
    "and what should my negative keyword list be this week; "
    "and which cities should I cut, hold or scale."
)


def _unset_settings(s: dict) -> list:
    out = []
    for k, v in s.items():
        if isinstance(v, dict):
            out += [f"{k}.{kk}" for kk, vv in v.items() if vv in (None, [], {})]
        elif v in (None, [], {}):
            out.append(k)
    return out


def _chips(items, prefix):
    """Clickable question buttons. A click stores the text and reruns."""
    items = [i for i in items if i]
    if not items:
        return
    cols = st.columns(min(len(items), 3))
    for i, text in enumerate(items):
        if cols[i % len(cols)].button(text, key=f"{prefix}_{i}", use_container_width=True):
            st.session_state[PENDING_KEY] = text
            st.rerun()


def _dumped(structs, user_text) -> str:
    """Markdown transcript of this answer for the download button."""
    parts = [f"## {user_text.strip()}", ""]
    for i, s in enumerate(structs):
        if len(structs) > 1:
            parts.append(f"### Part {i + 1} of {len(structs)}")
        if s["date"]:
            parts.append(f"_{s['date']}_")
        if s["head"]:
            parts.append(f"**{s['head']}**")
        for key in ("lead", "extra"):
            if s[key]:
                parts.append(s[key])
        for lbl, title, body in s["deep"]:
            parts.append(f"\n**{title}**\n{body}")
        if s["notes"]:
            parts.append(s["notes"])
        if s["tables"]:
            parts.append("\n**Supporting numbers**\n" + s["tables"])
    return "\n\n".join(parts)


def _show_one(s: dict, idx: int, part: int, latest: bool, with_chips: bool):
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
    if latest and with_chips:
        suggestions = list(s["suggestions"] or [])
        # Always offer "Why?" so the manager can go one level deeper in one click.
        if "Why?" not in suggestions:
            suggestions = ["Why?"] + suggestions
        st.caption("Ask next")
        _chips(suggestions, f"sug{idx}_{part}")


def _show(msg: dict, idx: int = 0, latest: bool = False):
    structs = msg.get("structs")
    if structs is None:
        s = msg.get("struct")
        if not s:
            st.markdown(msg.get("content", ""))
            return
        structs = [s]
    summary = msg.get("summary", "")
    if summary and len(structs) > 1:
        st.info(summary)
    for i, s in enumerate(structs):
        if len(structs) > 1:
            st.markdown(f"**Part {i + 1} of {len(structs)}**")
        _show_one(s, idx, i, latest=latest, with_chips=(i == len(structs) - 1))
        if i < len(structs) - 1:
            st.markdown("---")
    user_text = msg.get("user_text") or ""
    md = _dumped(structs, user_text)
    st.download_button("Download this answer", md, file_name=f"snack-diva-{idx}.md",
                       mime="text/markdown", key=f"dl_{idx}")


def _settings_panel():
    """Edit settings.yaml live. Covers the keys that gate the most answers."""
    s = settings_mod.load()
    with st.expander("⚙️ Settings (fill these to unlock recommendations)", expanded=False):
        with st.form("settings_form"):
            c1, c2 = st.columns(2)
            with c1:
                st.markdown("**Economics**")
                contrib = st.number_input(
                    "Contribution before ads (fraction of MRP GMV, e.g. 0.30)",
                    min_value=0.0, max_value=1.0, step=0.01,
                    value=float(s.get("contribution_before_ads_pct_of_mrp_gmv") or 0.0))
                a2s_ceiling = st.number_input(
                    "A2S ceiling (percent)", min_value=0.0, max_value=100.0, step=1.0,
                    value=float(s.get("a2s_ceiling_pct") or 0.0))
                target_a2s = st.number_input(
                    "Target A2S (percent)", min_value=0.0, max_value=100.0, step=1.0,
                    value=float(s.get("target_a2s_pct") or 0.0))
                monthly_budget = st.number_input(
                    "Monthly budget (₹)", min_value=0, step=10000,
                    value=int(s.get("monthly_budget_inr") or 0))
                monthly_target = st.number_input(
                    "Monthly GMV target (₹)", min_value=0, step=10000,
                    value=int(s.get("monthly_gmv_target_inr") or 0))
                lag_days = st.number_input(
                    "Attribution lag (days)", min_value=0, max_value=60, step=1,
                    value=int(s.get("lag_days") or 0))
            with c2:
                st.markdown("**Min data to judge a slice**")
                md = s.get("min_data") or {}
                md_spend = st.number_input("spend ≥ ₹", min_value=0, step=100,
                                           value=int(md.get("spend_inr") or 0))
                md_clicks = st.number_input("clicks ≥", min_value=0, step=5,
                                            value=int(md.get("clicks") or 0))
                md_days = st.number_input("days ≥", min_value=0, max_value=90, step=1,
                                          value=int(md.get("days") or 0))
                st.markdown("**Waste rule**")
                wr = s.get("waste_rule") or {}
                wr_spend = st.number_input("spend ≥ ₹", min_value=0, step=100,
                                           value=int(wr.get("spend_inr") or 0), key="wr_s")
                wr_roas = st.number_input("ROAS <", min_value=0.0, step=0.1,
                                          value=float(wr.get("roas_below") or 0.0))
                wr_days = st.number_input("over days ≥", min_value=0, step=1,
                                          value=int(wr.get("over_days") or 0))
                wr_zero = st.number_input("zero-GMV spend ≥ ₹", min_value=0, step=100,
                                          value=int(wr.get("zero_gmv_spend_inr") or 0))
                st.markdown("**Scale rule**")
                sr = s.get("scale_rule") or {}
                sr_roas = st.number_input("ROAS ≥", min_value=0.0, step=0.1,
                                          value=float(sr.get("roas_at_least") or 0.0), key="sr_r")
                sr_spend = st.number_input("spend ≥ ₹", min_value=0, step=100,
                                           value=int(sr.get("spend_inr") or 0), key="sr_s")
            st.markdown("**Attack / defend / expand share (percent, 0 skips)**")
            c3, c4, c5 = st.columns(3)
            spl = s.get("split_target") or {}
            tgt_a = c3.number_input("Attack %", min_value=0, max_value=100, step=5,
                                    value=int(spl.get("attack") or 0))
            tgt_e = c4.number_input("Expand %", min_value=0, max_value=100, step=5,
                                    value=int(spl.get("expand") or 0))
            tgt_d = c5.number_input("Defend %", min_value=0, max_value=100, step=5,
                                    value=int(spl.get("defend") or 0))
            if st.form_submit_button("Save settings"):
                def _nz(x):
                    return x if x else None
                s["contribution_before_ads_pct_of_mrp_gmv"] = _nz(contrib)
                s["a2s_ceiling_pct"] = _nz(a2s_ceiling)
                s["target_a2s_pct"] = _nz(target_a2s)
                s["monthly_budget_inr"] = _nz(monthly_budget)
                s["monthly_gmv_target_inr"] = _nz(monthly_target)
                s["lag_days"] = _nz(lag_days)
                s["min_data"] = {"spend_inr": _nz(md_spend), "clicks": _nz(md_clicks), "days": _nz(md_days)}
                s["waste_rule"] = {"spend_inr": _nz(wr_spend), "roas_below": _nz(wr_roas),
                                    "over_days": _nz(wr_days), "zero_gmv_spend_inr": _nz(wr_zero)}
                s["scale_rule"] = {"roas_at_least": _nz(sr_roas), "spend_inr": _nz(sr_spend)}
                if any((tgt_a, tgt_e, tgt_d)):
                    s["split_target"] = {"attack": _nz(tgt_a), "expand": _nz(tgt_e), "defend": _nz(tgt_d)}
                settings_mod.save(s)
                st.success("Saved.")
                st.rerun()


def _sku_panel():
    """Propose short SKU names from the loaded product titles."""
    with st.expander("🏷️ Auto-learn short SKU names"):
        try:
            ctx = Context.open()
            raw = sorted(ctx.catalog().products)
        except Exception as e:
            st.caption(f"Could not read catalog: {type(e).__name__}")
            return
        finally:
            try:
                ctx.close()
            except Exception:
                pass
        s = settings_mod.load()
        existing = s.get("product_title_to_sku") or {}
        proposals = sku_learn.propose(raw, existing=existing)
        if not proposals:
            st.caption("Every loaded product title already has a short name.")
            return
        st.caption(f"{len(proposals)} new titles. Edit the short name or clear it to skip, then save all.")
        edits = {}
        for i, (title, suggested) in enumerate(proposals[:50]):
            cols = st.columns([3, 2])
            cols[0].text(title[:90])
            edits[title] = cols[1].text_input("short", value=suggested, key=f"sku_{i}", label_visibility="collapsed")
        if st.button("Save SKU names"):
            new_map = dict(existing)
            for title, name in edits.items():
                nm = (name or "").strip()
                if nm:
                    new_map[title] = nm
            s["product_title_to_sku"] = new_map
            settings_mod.save(s)
            st.success(f"Saved {len(new_map)} entries.")
            st.rerun()


def _run_alerts_once():
    """Show load-time alerts on first render after a session starts, then once
    per session unless the data path changes."""
    if st.session_state.get(ALERT_KEY):
        return
    try:
        ctx = Context.open()
        try:
            msgs = alerts.build(ctx)
        finally:
            ctx.close()
    except Exception:
        msgs = []
    st.session_state[ALERT_KEY] = True
    for level, msg in msgs:
        (st.warning if level == "warn" else st.info)(msg)


def render():
    st.subheader("💅 Snackibella")
    st.caption("Ask in plain English. Follow-ups like \"why?\", \"and ROAS?\", \"and for Mumbai?\" and compound questions "
               "like \"which city spends most and which SKUs are weak\" all work. Every answer is a deep dive: what, why, "
               "how, whom, similarity, cause and what to do next. All computed locally from the loaded Instamart ads data.")
    if HISTORY_KEY not in st.session_state:
        st.session_state[HISTORY_KEY] = []
    hist = st.session_state[HISTORY_KEY]

    _run_alerts_once()
    _settings_panel()
    _sku_panel()

    with st.expander("What can I ask? / settings status"):
        st.markdown("Any question about spend, impressions, clicks, GMV, ROAS, CPM, CPC, cities, SKUs, keywords, search queries, "
                    "placements or dates works, including trends, comparisons and row-level lookups. The full list is in `bot/questions.md`.")
        unset = _unset_settings(settings_mod.load())
        if unset:
            st.markdown(f"{len(unset)} settings in `bot/settings.yaml` are still empty. Answers that need them say `needs setting: <name>`.")

    cols = st.columns([1, 1, 2])
    if cols[0].button("📋 Weekly checkup", use_container_width=True):
        st.session_state[PENDING_KEY] = WEEKLY_CHECKUP
        st.rerun()
    if hist and cols[1].button("Clear chat", use_container_width=True):
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

    prompt = st.chat_input("Ask Snackibella about the Instamart ads data") or st.session_state.pop(PENDING_KEY, None)
    if prompt:
        hist.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)
        with st.chat_message("assistant"):
            with st.spinner("Working it out from the data..."):
                try:
                    results = engine.ask_many(prompt, prev=st.session_state.get(PREV_KEY))
                    structs = [answers.render_struct(r) for r in results]
                    for r in results:
                        if r.meta:
                            st.session_state[PREV_KEY] = r.meta
                    msg = {"role": "assistant", "structs": structs, "user_text": prompt,
                           "summary": multi.compound_summary(results)}
                except Exception:
                    msg = {"role": "assistant", "content": "Something went wrong answering that. Try rephrasing it."}
            hist.append(msg)
        st.rerun()
