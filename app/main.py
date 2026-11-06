"""CareerPilot: private, single-user Streamlit application.
Not hardened for multi-user or regulated/confidential client data.

Default AI_PROVIDER is 'openai': tailored resumes and STAR answers are generated in the app with
Pydantic-validated structured outputs, checked deterministically against the user's own sources, and
every paid call is an explicit button click with a cost estimate and guard. AI_PROVIDER='manual' keeps
the copy/paste fallback that needs no API key.
"""
import hmac
import json
import os
import sys

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from careerpilot import generation, library  # noqa: E402
from careerpilot.config import (ai_provider, configure_logging, get_setting, openai_settings,  # noqa: E402
                                password_status, setting_diagnostics)
from careerpilot.documents import DOCX_MIME, docx_bytes, extract, resume_docx_bytes  # noqa: E402
from careerpilot.grounding import (approved_resume_text, claim_id_map, evidence_sources,  # noqa: E402
                                   interview_text, rank_evidence, resume_sources, review_interview,
                                   review_resume, numbers_in)
from careerpilot.llm import LLMError, OpenAIRunner  # noqa: E402
from careerpilot.pricing import estimate_cost, guard, load_prices  # noqa: E402
from careerpilot.prompts import (INTERVIEW_CHECKLIST, RESUME_CHECKLIST, build_interview_prompt,  # noqa: E402
                                 build_resume_prompt, prompt_size_warning, unresolved_markers)
from careerpilot.schemas import InterviewPack, ResumeDraft  # noqa: E402
from careerpilot.storage import Storage, StorageError, connection_profile  # noqa: E402

st.set_page_config(page_title="CareerPilot AI", page_icon="📁", layout="wide")
logger = configure_logging()
SELF_CHECK_NOTE = ("The AI evidence check is a second pass by a language model over the same sources. It can "
                   "miss problems and is **not independent proof**. You are the final verifier of every claim.")


def _kv(data):
    return " ".join(f"{k}={v}" for k, v in data.items())


@st.cache_resource(show_spinner=False)
def get_storage(url, sslmode):
    # Only value-free facts are logged: where DATABASE_URL came from and its shape, never its content.
    logger.info("database_config %s", _kv(setting_diagnostics("DATABASE_URL")))
    logger.info("database_profile %s", _kv(connection_profile(url)))
    store = Storage(url, sslmode)
    store.check_connection()
    store.init_schema()
    logger.info("database_ready sslmode=%s", store.effective_sslmode)
    return store


# --- shared review helpers --------------------------------------------------------------------------

def checklist_gate(kind, checklist):
    st.markdown("**Claim review checklist**")
    confirmed = [st.checkbox(item, key=f"{kind}_check_{i}") for i, item in enumerate(checklist)]
    if not all(confirmed):
        st.caption("Tick every checklist item to enable the Word download.")
    return all(confirmed)


def review_draft(kind, checklist, file_name, download_label):
    """Manual mode: paste-back, edit, claim-review checklist and Word download."""
    edit_key = f"{kind}_edit"
    with st.form(f"{kind}_paste_form", clear_on_submit=True):
        pasted = st.text_area("Paste the AI result here", height=200,
                              help="Paste the full response from ChatGPT or Claude, including the claim ledger.")
        if st.form_submit_button("Load pasted result for review"):
            if pasted.strip():
                st.session_state[edit_key] = pasted
            else:
                st.warning("Nothing was pasted.")
    if not st.session_state.get(edit_key):
        return
    edited = st.text_area("Review and edit before download", key=edit_key, height=480)
    markers = unresolved_markers(edited)
    if markers:
        st.warning(f"{len(markers)} [VERIFY] marker(s) remain. Confirm each against your evidence, or delete it.")
    ok = checklist_gate(kind, checklist)
    st.download_button(download_label, data=docx_bytes(edited), file_name=file_name, mime=DOCX_MIME,
                       disabled=not ok, key=f"{kind}_download")


def prompt_panel(kind):
    prompt = st.session_state.get(f"{kind}_prompt")
    if not prompt:
        return
    warning = prompt_size_warning(prompt)
    if warning:
        st.warning(warning)
    with st.expander("Prompt to copy into ChatGPT Plus or Claude Pro", expanded=True):
        st.caption("Use the copy icon at the top right of the box. Start a new chat for each prompt.")
        st.code(prompt, language=None, wrap_lines=True)


def evidence_as_text(records):
    return "\n\n".join(f"[{sid}] {text}" for sid, text in evidence_sources(records).items())


def select_resume(resumes, key):
    if not resumes:
        st.warning("Upload a master resume in the Career evidence tab first.")
        return None
    names = {r["id"]: f"{r['name']} (saved {r['saved_at'][:10]})" for r in resumes}
    chosen = st.selectbox("Master resume", list(names), format_func=names.get, index=len(resumes) - 1, key=key)
    return next(r for r in resumes if r["id"] == chosen)


# --- paid API calls ---------------------------------------------------------------------------------

def note_usage(usage):
    st.session_state.setdefault("usage_log", []).append(usage.as_record())
    try:
        library.record_usage(store, usage.as_record())
    except StorageError:
        pass  # usage display still works for this session


def paid_button(label, key, build, model, effort):
    """Show the cost estimate and guard, then make exactly one API call when the user clicks.

    build: zero-argument function returning a generation.Operation (may raise ValueError).
    Returns the validated Pydantic object, or None.
    """
    try:
        op = build()
    except ValueError as exc:
        st.info(str(exc))
        return None
    estimate = estimate_cost(prices, model, op.estimate_text, max_tokens)
    st.caption(f"Estimate before calling: {estimate.label}")
    allow_unknown = False
    if estimate.max_cost is None:
        allow_unknown = st.checkbox(f"Proceed without a known price for {model}", key=f"{key}_unknown")
    reason = guard(estimate, cost_limit, allow_unknown)
    if reason:
        st.warning(reason)
    last = st.session_state.get(f"{key}_last")
    clicked = st.button(label, key=key, disabled=bool(reason) or not api_ready, type="primary")
    if last:
        st.caption(f"Last run: {last}")
    if not clicked:
        return None
    try:
        runner = OpenAIRunner(get_setting("OPENAI_API_KEY"), prices)
        with st.spinner(f"Calling {model}..."):
            parsed, usage = runner.run(op.name, model, op.instructions, op.input_text, op.schema, max_tokens, effort)
    except LLMError as exc:
        if exc.usage:
            note_usage(exc.usage)
            st.session_state[f"{key}_last"] = f"failed, still billed: {exc.usage.label}"
        st.error(exc.message)
        return None
    if usage:
        note_usage(usage)
        st.session_state[f"{key}_last"] = usage.label
        st.success(f"Actual cost: {usage.label}")
    else:
        st.session_state[f"{key}_last"] = "usage data not returned"
    return parsed


# --- app start: settings, auth, sidebar, storage ----------------------------------------------------

st.title("CareerPilot AI")
st.caption("Private career evidence, resume tailoring, and interview preparation")

try:
    provider = ai_provider()
    ai = openai_settings()
    prices = load_prices(ai["prices_json"])
except ValueError as exc:
    st.error(str(exc))
    st.stop()

password = get_setting("APP_PASSWORD")
pw_status = password_status(password)
if pw_status == "weak":
    st.error("APP_PASSWORD is set but shorter than 16 characters. Use a longer password or remove it.")
    st.stop()
if pw_status == "enabled" and not st.session_state.get("authenticated", False):
    with st.form("login"):
        entered = st.text_input("Workspace password", type="password")
        submitted = st.form_submit_button("Sign in")
    if submitted:
        if hmac.compare_digest(entered.encode(), password.encode()):
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Incorrect password")
    st.stop()

api_ready = provider == "openai" and bool(get_setting("OPENAI_API_KEY"))
with st.sidebar:
    if pw_status == "enabled":
        st.success("Signed in")
        if st.button("Sign out"):
            st.session_state.clear()
            st.rerun()
    else:
        st.info("No app password set. Access relies on your host's viewer controls "
                "(Streamlit Community Cloud private app).")
    if provider == "manual":
        st.caption("AI mode: **manual** (copy/paste, no API key needed)")
    else:
        st.caption(f"AI mode: **OpenAI API**. Drafting: `{ai['model']}` · matching & checks: `{ai['fast_model']}`")
        if not api_ready:
            st.error("OPENAI_API_KEY is not configured. Add it to Streamlit secrets to enable generation.")
        st.markdown("**Cost controls**")
        max_tokens = st.number_input("Max output tokens per call", 1_000, 128_000, ai["max_output_tokens"], 1_000,
                                     help="Includes reasoning tokens. Higher limits allow longer outputs and raise "
                                          "the worst-case cost.")
        cost_limit = st.number_input("Cost guard per operation (USD)", 0.0, ai["max_cost"], ai["max_cost"], 0.01,
                                     format="%.2f", help="Calls whose worst-case estimate exceeds this are blocked. "
                                                         "The maximum comes from OPENAI_MAX_COST_PER_OPERATION.")
        session_cost = sum(u["cost_usd"] or 0 for u in st.session_state.get("usage_log", []))
        st.caption(f"This session: {len(st.session_state.get('usage_log', []))} API call(s), ${session_cost:.4f}")
    st.caption("Single-user MVP. Do not upload confidential employer/client documents.")

try:
    store = get_storage(get_setting("DATABASE_URL"), get_setting("DATABASE_SSLMODE"))
except StorageError as exc:
    st.error(exc.hint)
    st.caption(f"Diagnostic: category={exc.category}, error type={exc.exc_type or 'n/a'}. "
               "Details (without credentials) are in the app logs.")
    if st.button("Retry database connection"):
        st.rerun()
    st.stop()


# --- tabs -------------------------------------------------------------------------------------------

def career_evidence_tab():
    st.subheader("Master resumes")
    resumes = library.load_resumes(store)
    upload = st.file_uploader("Upload a resume (PDF, DOCX or TXT)", type=["pdf", "docx", "txt"])
    resume_name = st.text_input("Name for this resume", placeholder="e.g. Program management - 2026")
    if upload is not None and st.button("Extract and save resume"):
        try:
            content = extract(upload.name, upload.getvalue())
        except Exception as exc:
            content = None
            st.error(f"Unable to read resume: {exc}")
        if content is not None:
            if len(content.strip()) < 100:
                st.warning("Very little text was extracted. Try a DOCX or text-based PDF.")
            else:
                resumes = library.add_resume(store, resume_name or upload.name, content)
                st.success("Resume text saved to your configured database.")
    for resume in resumes:
        with st.expander(f"{resume['name']} (saved {resume['saved_at'][:10]})"):
            st.text_area("Resume text", value=resume["text"], height=250, disabled=True, key=f"rt_{resume['id']}")
            if st.button("Delete this resume", key=f"del_{resume['id']}"):
                library.delete_resume(store, resume["id"])
                st.rerun()

    st.subheader("Evidence records")
    st.caption("One row per project, achievement or STAR story. Only rows ticked **Verified** are used as "
               "evidence. Set **Ownership** honestly: work recorded as *Team* cannot be claimed as personal "
               "ownership in generated output.")
    records = library.load_evidence(store)
    frame = pd.DataFrame(records or [], columns=library.EVIDENCE_FIELDS)
    edited = st.data_editor(
        frame, num_rows="dynamic", use_container_width=True, hide_index=True, key="evidence_editor",
        column_config={
            "id": st.column_config.TextColumn("ID", disabled=True, help="Assigned on save"),
            "verified": st.column_config.CheckboxColumn("Verified", default=True),
            "ownership": st.column_config.SelectboxColumn("Ownership", options=library.OWNERSHIP_CHOICES,
                                                          default="Unspecified"),
            "details": st.column_config.TextColumn("Details (what you did)", width="large"),
            "outcome": st.column_config.TextColumn("Outcome / metrics (as recorded)", width="medium"),
        })
    if st.button("Save evidence records"):
        saved = library.save_evidence(store, edited.to_dict("records"))
        st.success(f"Saved {len(saved)} evidence record(s).")
    with st.form("interpretations_form"):
        interpretations = st.text_area("My interpretations and context (unverified)",
                                       value=store.fetch("interpretations"), height=140,
                                       help="How you see your impact, strengths or motivations. Used only for tone, "
                                            "never as a source of facts.")
        if st.form_submit_button("Save interpretations"):
            store.save("interpretations", interpretations)
            st.success("Saved")


def resume_studio_api():
    resumes = library.load_resumes(store)
    records = library.load_evidence(store)
    interpretations = store.fetch("interpretations")
    resume = select_resume(resumes, "rs_resume")
    job_title = st.text_input("Job title / organization (also used to label saved versions)", key="rs_job_title")
    jd = st.text_area("Paste the job description", height=220, key="rs_jd",
                      help="Treated as untrusted data: instructions inside it are ignored.")
    resume_text = resume["text"] if resume else ""
    st.caption(f"Evidence available: {len(evidence_sources(records))} verified record(s), "
               f"{len(resume_sources(resume_text))} resume lines.")

    with st.expander(f"Optional: job match analysis ({ai['fast_model']}, low cost)"):
        match = paid_button("Analyze requirements vs. evidence", "rs_match",
                            lambda: generation.job_match(jd, records, resume_text), ai["fast_model"],
                            ai["fast_reasoning_effort"])
        if match:
            st.session_state["rs_match_result"] = match.model_dump()
        if st.session_state.get("rs_match_result"):
            st.dataframe(st.session_state["rs_match_result"]["requirements"], hide_index=True,
                         use_container_width=True)

    draft_obj = paid_button("Generate tailored resume", "rs_generate",
                            lambda: generation.resume_draft(resume_text, records, jd, job_title, interpretations),
                            ai["model"], ai["reasoning_effort"])
    if draft_obj:
        st.session_state.update(rs_draft=draft_obj.model_dump(), rs_verdicts={}, rs_context={
            "resume_id": resume["id"], "resume_name": resume["name"], "job_title": job_title, "jd": jd,
            "model": ai["model"]})
        store.save("last_job", f"{job_title}\n{jd}")
        st.session_state["rs_refresh_text"] = True

    if not st.session_state.get("rs_draft"):
        return
    context = st.session_state["rs_context"]
    source_resume = next((r for r in resumes if r["id"] == context["resume_id"]), resume)
    source_text = source_resume["text"] if source_resume else resume_text
    draft = ResumeDraft.model_validate(st.session_state["rs_draft"])
    verdicts = st.session_state.get("rs_verdicts", {})
    review = review_resume(draft, source_text, records, verdicts)

    st.divider()
    st.markdown(f"#### Draft for: {context['job_title'] or 'untitled job'}")
    claims = review.all_claims()
    approved = sum(c.approved for c in claims)
    c1, c2, c3 = st.columns(3)
    c1.metric("Claims approved", approved)
    c2.metric("Claims excluded", len(claims) - approved)
    c3.metric("JD gaps (not filled)", len(review.gaps))
    for warning in review.warnings:
        st.warning(warning)
    st.markdown("**Claim-to-evidence mapping**")
    st.dataframe([{"ID": c.claim_id, "Status": "Approved" if c.approved else "Excluded", "Claim": c.text,
                   "Evidence": ", ".join(c.evidence_ids), "Issues": "; ".join(c.issues),
                   "AI self-check": c.self_check or "not run"} for c in claims],
                 hide_index=True, use_container_width=True)
    for entry in review.entries:
        if entry.issues:
            st.error(f"Role excluded: {entry.label}. " + "; ".join(entry.issues))
    if review.gaps:
        with st.expander("Job requirements without supporting evidence"):
            st.write("\n".join(f"- {g}" for g in review.gaps))

    with st.expander(f"Evidence check (AI self-review with {ai['fast_model']})"):
        st.info(SELF_CHECK_NOTE)
        id_map = claim_id_map(draft)
        check = paid_button("Run AI evidence check", "rs_check", lambda: generation.evidence_check(
            {cid: f"{c.text} [cites: {', '.join(c.evidence_ids) or 'none'}]" for cid, c in id_map.items()},
            source_text, records), ai["fast_model"], ai["fast_reasoning_effort"])
        if check:
            st.session_state["rs_verdicts"] = {v.claim_id: (v.verdict, v.reason) for v in check.verdicts}
            st.session_state["rs_refresh_text"] = True
            st.rerun()

    with st.expander("Regenerate one section"):
        options = ["summary", "skills"] + [f"experience:{i}" for i in range(len(draft.experience))]
        labels = {"summary": "Summary", "skills": "Skills",
                  **{f"experience:{i}": f"Experience: {e.title} | {e.employer}" for i, e in enumerate(draft.experience)}}
        target = st.selectbox("Section", options, format_func=labels.get, key="rs_section")
        instruction = st.text_input("Instruction (optional)", key="rs_instruction",
                                    placeholder="e.g. emphasise stakeholder management")
        section, _, index = target.partition(":")
        current = (draft.experience[int(index)].model_dump() if section == "experience"
                   else {section: [x.model_dump() for x in getattr(draft, section)]})
        result = paid_button("Regenerate section", "rs_regen", lambda: generation.regenerate_section(
            section, json.dumps(current), source_text, records, context["jd"], instruction, interpretations),
            ai["model"], ai["reasoning_effort"])
        if result:
            data = st.session_state["rs_draft"]
            if section == "experience":
                original = data["experience"][int(index)]
                new_entry = result.entry.model_dump()
                # Employer, title and dates always stay exactly as in the original draft.
                new_entry.update({k: original[k] for k in ("employer", "title", "dates")})
                data["experience"][int(index)] = new_entry
            else:
                data[section] = [x.model_dump() for x in getattr(result, section)]
            st.session_state.update(rs_draft=data, rs_verdicts={}, rs_refresh_text=True)
            st.rerun()

    if st.session_state.pop("rs_refresh_text", False) or "rs_text" not in st.session_state:
        st.session_state["rs_text"] = approved_resume_text(draft, review)
    st.markdown("**Approved resume (only claims that passed every check)**")
    st.caption("Regenerating or running the evidence check replaces this text with fresh approved output.")
    text = st.text_area("Edit before saving or downloading", key="rs_text", height=520)
    all_sources = " ".join(list(resume_sources(source_text).values()) + list(evidence_sources(records).values()))
    new_numbers = sorted(numbers_in(text) - numbers_in(all_sources))
    if new_numbers:
        st.warning(f"Your edited text contains number(s) not found in your sources: {', '.join(new_numbers)}")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Save version for this job"):
            library.save_version(store, context["job_title"], context["resume_name"], st.session_state["rs_draft"],
                                 text, context["model"], {"approved": approved, "excluded": len(claims) - approved})
            st.success("Version saved")
    with col2:
        ok = checklist_gate("rs", RESUME_CHECKLIST)
        st.download_button("Download resume (Word)", resume_docx_bytes(text), "careerpilot-tailored-resume.docx",
                           DOCX_MIME, disabled=not ok)


def saved_versions():
    versions = library.load_list(store, "resume_versions")
    with st.expander(f"Saved resume versions ({len(versions)})"):
        for version in reversed(versions):
            st.markdown(f"**{version['job']}** · {version['created_at'][:16]} · {version['model']} · "
                        f"from '{version['resume']}'")
            c1, c2 = st.columns(2)
            c1.download_button("Download (Word)", resume_docx_bytes(version["approved_text"]),
                               f"careerpilot-{version['id']}.docx", DOCX_MIME, key=f"dl_{version['id']}")
            if c2.button("Delete", key=f"delv_{version['id']}"):
                library.delete_version(store, version["id"])
                st.rerun()


def interview_coach_api():
    resumes = library.load_resumes(store)
    records = library.load_evidence(store)
    interpretations = store.fetch("interpretations")
    resume = select_resume(resumes, "ic_resume")
    resume_text = resume["text"] if resume else ""
    mode = st.radio("Prepare from", ["job", "question"], horizontal=True, key="ic_mode",
                    format_func={"job": "A job description", "question": "A specific interview question"}.get)
    default_job = store.fetch("last_job") if mode == "job" else ""
    query = st.text_area("Job description" if mode == "job" else "Interview question", value=default_job,
                         height=180, key=f"ic_query_{mode}")
    focus = st.text_input("Focus (optional)", key="ic_focus",
                          placeholder="e.g. leadership, stakeholder management, conflict resolution")
    count = st.slider("Number of questions", 1, 10, 5, key="ic_count") if mode == "job" else 1

    st.markdown("**Relevant verified evidence**")
    ranked = rank_evidence(f"{query} {focus}", records, limit=len(records) or 1)
    ai_ranking = st.session_state.get("ic_ai_rank")
    if ai_ranking:
        order = {rid: i for i, rid in enumerate(ai_ranking)}
        ranked.sort(key=lambda pair: order.get(pair[0]["id"], len(order)))
    st.dataframe([{"ID": r["id"], "Score": score, "Title": r["title"], "Employer": r["employer"],
                   "Ownership": r["ownership"]} for r, score in ranked], hide_index=True, use_container_width=True)
    default_ids = [r["id"] for r, score in ranked[:6] if score > 0 or ai_ranking]
    selected_ids = st.multiselect("Evidence to use", [r["id"] for r, _ in ranked], default=default_ids,
                                  key=f"ic_selected_{hash(tuple(default_ids))}")
    with st.expander(f"Optional: rank evidence with {ai['fast_model']} (low cost)"):
        st.caption("Free keyword ranking is used by default. This makes one paid call.")
        match = paid_button("Rank evidence with AI", "ic_match", lambda: generation.job_match(query, records,
                            resume_text), ai["fast_model"], ai["fast_reasoning_effort"])
        if match:
            st.session_state["ic_ai_rank"] = [i for i in match.ranked_evidence_ids if i in evidence_sources(records)]
            st.rerun()

    chosen = [r for r in records if r["id"] in selected_ids]
    pack = paid_button("Generate STAR answers", "ic_generate", lambda: generation.interview_pack(
        chosen, resume_text, query, mode, count, focus, interpretations), ai["model"], ai["reasoning_effort"])
    if pack:
        st.session_state.update(ic_pack=pack.model_dump(), ic_verdicts={}, ic_records=chosen,
                                ic_resume_text=resume_text, ic_refresh_text=True)

    if not st.session_state.get("ic_pack"):
        return
    pack = InterviewPack.model_validate(st.session_state["ic_pack"])
    used_records = st.session_state["ic_records"]
    reviews = review_interview(pack, used_records, st.session_state["ic_resume_text"],
                               st.session_state.get("ic_verdicts"))
    st.divider()
    for i, (answer, review) in enumerate(zip(pack.answers, reviews), 1):
        flag = "✅" if not review.issues else f"⚠️ {len(review.issues)} flag(s)"
        with st.expander(f"Q{i}. {answer.question}  {flag}", expanded=i == 1):
            st.caption(f"Why they ask: {answer.why_asked} · Evidence: {', '.join(answer.evidence_ids) or 'none'}")
            st.markdown(f"**Situation:** {answer.situation}\n\n**Task:** {answer.task}\n\n"
                        f"**Action:** {answer.action}\n\n**Result:** {answer.result}")
            st.markdown(f"**Spoken version ({review.spoken_words} words, ~{review.spoken_words * 60 // 150} s):**")
            st.write(answer.spoken_version)
            st.markdown("**Likely follow-ups:**\n" + "\n".join(f"- {q}" for q in answer.follow_up_questions))
            for issue in review.issues:
                st.warning(issue)
    if pack.evidence_gaps:
        st.markdown("**Evidence gaps to prepare:**\n" + "\n".join(f"- {g}" for g in pack.evidence_gaps))

    with st.expander(f"Evidence check (AI self-review with {ai['fast_model']})"):
        st.info(SELF_CHECK_NOTE)
        claims = {c.claim_id: f"{c.text} [cites: {', '.join(c.evidence_ids) or 'none'}]"
                  for r in reviews for c in r.claims}
        check = paid_button("Run AI evidence check", "ic_check", lambda: generation.evidence_check(
            claims, st.session_state["ic_resume_text"], used_records), ai["fast_model"], ai["fast_reasoning_effort"])
        if check:
            st.session_state.update(ic_verdicts={v.claim_id: (v.verdict, v.reason) for v in check.verdicts},
                                    ic_refresh_text=True)
            st.rerun()

    if st.session_state.pop("ic_refresh_text", False) or "ic_text" not in st.session_state:
        st.session_state["ic_text"] = interview_text(pack, reviews)
    text = st.text_area("Edit before download", key="ic_text", height=480)
    ok = checklist_gate("ic", INTERVIEW_CHECKLIST)
    st.download_button("Download interview prep (Word)", docx_bytes(text), "careerpilot-interview-prep.docx",
                       DOCX_MIME, disabled=not ok)


def resume_studio_manual():
    resume = select_resume(library.load_resumes(store), "rsm_resume")
    records = library.load_evidence(store)
    with st.form("resume_form"):
        job_title = st.text_input("Job title / organization")
        jd = st.text_area("Paste the job description", height=250)
        tailor = st.form_submit_button("Build resume prompt")
    if tailor:
        try:
            prompt = build_resume_prompt(resume["text"] if resume else "", evidence_as_text(records), jd, job_title,
                                         store.fetch("interpretations"))
        except ValueError as exc:
            st.warning(str(exc))
        else:
            store.save("last_job", f"{job_title}\n{jd}")
            st.session_state["resume_prompt"] = prompt
    prompt_panel("resume")
    review_draft("resume", RESUME_CHECKLIST, "careerpilot-tailored-resume.docx", "Download editable Word draft")


def interview_coach_manual():
    resume = select_resume(library.load_resumes(store), "icm_resume")
    records = library.load_evidence(store)
    with st.form("interview_form"):
        interview_job = st.text_area("Job description (optional: uses your last resume job if blank)", height=180)
        focus = st.text_input("Interview focus", value="Leadership, stakeholder management, AI delivery, conflict resolution")
        count = st.slider("Number of questions", 3, 12, 6)
        generate = st.form_submit_button("Build interview prompt")
    if generate:
        try:
            prompt = build_interview_prompt(resume["text"] if resume else "", evidence_as_text(records),
                                            interview_job.strip() or store.fetch("last_job"), focus, count,
                                            store.fetch("interpretations"))
        except ValueError as exc:
            st.warning(str(exc))
        else:
            st.session_state["interview_prompt"] = prompt
    prompt_panel("interview")
    review_draft("interview", INTERVIEW_CHECKLIST, "careerpilot-interview-prep.docx", "Download interview prep (Word)")


def job_tracker_tab():
    st.subheader("Job tracking")
    st.info("This release supports manual tracking. Automated job discovery and submissions are deliberately separate follow-on modules.")
    with st.form("job_track"):
        company = st.text_input("Company")
        title = st.text_input("Role")
        url = st.text_input("Official application URL")
        status = st.selectbox("Status", ["Saved", "Preparing", "Ready for review", "Applied", "Interview", "Closed"])
        if st.form_submit_button("Save job"):
            store.add_job(company, title, url, status)
            st.success("Job saved")
    jobs = store.load_jobs()
    if jobs:
        st.dataframe(jobs, hide_index=True, use_container_width=True)
    st.download_button("Export tracked jobs (JSON)", json.dumps(jobs, indent=2), file_name="careerpilot-jobs.json")


def data_management():
    with st.expander("Data management"):
        usage = library.load_list(store, "api_usage")
        if usage:
            total = sum(u.get("cost_usd") or 0 for u in usage)
            st.markdown(f"**API usage log**: {len(usage)} call(s), ${total:.4f} total (token counts and cost only, "
                        "no content)")
            st.dataframe(usage[-50:], hide_index=True, use_container_width=True)
        st.markdown("**Private export**: download everything saved in the database (resumes, evidence, "
                    "interpretations, resume versions, job history, usage log). Store the file somewhere private.")
        if st.button("Prepare full data export"):
            st.session_state["export_json"] = json.dumps(store.export_all(), indent=2)
        if st.session_state.get("export_json"):
            st.download_button("Download data export (JSON)", st.session_state["export_json"],
                               file_name="careerpilot-export.json", mime="application/json",
                               on_click=lambda: st.session_state.pop("export_json", None))
        st.warning("These actions permanently remove the selected records from the active database, but not necessarily provider backups.")
        if st.button("Delete saved resumes and career evidence"):
            for key in ("resume", "evidence", "interpretations", "master_resumes", "evidence_records"):
                store.delete(key)
            st.success("Current database records deleted")
        if st.button("Delete resume versions"):
            store.delete("resume_versions")
            st.success("Resume versions deleted")
        if st.button("Delete job history"):
            for key in ("tracked_jobs", "last_job"):
                store.delete(key)
            st.success("Job history deleted")


def render():
    tab1, tab2, tab3, tab4 = st.tabs(["Career evidence", "Resume studio", "Interview coach", "Job tracker"])
    with tab1:
        career_evidence_tab()
    with tab2:
        st.subheader("Tailor resume for a job")
        if provider == "openai":
            resume_studio_api()
            saved_versions()
        else:
            resume_studio_manual()
    with tab3:
        st.subheader("Interview question & answer studio")
        interview_coach_api() if provider == "openai" else interview_coach_manual()
    with tab4:
        job_tracker_tab()
    st.divider()
    data_management()


try:
    render()
except StorageError as exc:
    st.error(exc.hint)
    if st.button("Retry"):
        st.rerun()
