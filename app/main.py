"""CareerPilot MVP: private, single-user Streamlit application.
Not hardened for multi-user or regulated/confidential client data.

Default AI_PROVIDER is 'manual': the app builds evidence-grounded prompts to copy into ChatGPT Plus or
Claude Pro, and the result is pasted back for review. AI_PROVIDER='openai' re-enables the API (opt-in).
"""
import hmac
import json
import os
import sys

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from careerpilot import ai_client  # noqa: E402
from careerpilot.config import (DEFAULT_OPENAI_MODEL, ai_provider, configure_logging, get_setting,  # noqa: E402
                                password_status, setting_diagnostics)
from careerpilot.documents import DOCX_MIME, docx_bytes, extract  # noqa: E402
from careerpilot.prompts import (INTERVIEW_CHECKLIST, RESUME_CHECKLIST, build_interview_prompt,  # noqa: E402
                                 build_resume_prompt, prompt_size_warning, unresolved_markers)
from careerpilot.storage import Storage, StorageError, connection_profile  # noqa: E402

st.set_page_config(page_title="CareerPilot AI", page_icon="📁", layout="wide")
logger = configure_logging()


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


def review_draft(kind, checklist, file_name, download_label):
    """Paste-back, edit, claim-review checklist and Word download for one generated draft."""
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
    st.markdown("**Claim review checklist**")
    confirmed = [st.checkbox(item, key=f"{kind}_check_{i}") for i, item in enumerate(checklist)]
    if not all(confirmed):
        st.caption("Tick every checklist item to enable the Word download.")
    st.download_button(download_label, data=docx_bytes(edited), file_name=file_name, mime=DOCX_MIME,
                       disabled=not all(confirmed), key=f"{kind}_download")


def show_prompt(kind, prompt, provider):
    st.session_state[f"{kind}_prompt"] = prompt
    if provider == "openai":
        with st.spinner("Drafting with the OpenAI API..."):
            try:
                result = ai_client.generate(prompt, get_setting("OPENAI_API_KEY"),
                                            get_setting("OPENAI_MODEL", DEFAULT_OPENAI_MODEL))
                st.session_state[f"{kind}_edit"] = result
            except ai_client.AIConfigError as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error(f"AI request failed ({type(exc).__name__}). You can still copy the prompt below.")


def prompt_panel(kind):
    prompt = st.session_state.get(f"{kind}_prompt")
    if not prompt:
        return
    warning = prompt_size_warning(prompt)
    if warning:
        st.warning(warning)
    with st.expander("Prompt to copy into ChatGPT Plus or Claude Pro", expanded=provider == "manual"):
        st.caption("Use the copy icon at the top right of the box. Start a new chat for each prompt.")
        st.code(prompt, language=None, wrap_lines=True)


st.title("CareerPilot AI")
st.caption("Private career evidence, resume tailoring, and interview preparation")

try:
    provider = ai_provider()
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

with st.sidebar:
    if pw_status == "enabled":
        st.success("Signed in")
        if st.button("Sign out"):
            st.session_state.clear()
            st.rerun()
    else:
        st.info("No app password set. Access relies on your host's viewer controls "
                "(Streamlit Community Cloud private app).")
    st.caption(f"AI mode: **{provider}**" + (" (copy/paste, no API key needed)" if provider == "manual" else " (API)"))
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


def render():
    tab1, tab2, tab3, tab4 = st.tabs(["Career evidence", "Resume studio", "Interview coach", "Job tracker"])
    with tab1:
        st.subheader("Master resume")
        upload = st.file_uploader("Upload a resume (PDF, DOCX or TXT)", type=["pdf", "docx", "txt"])
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
                    store.save("resume", content)
                    st.success("Resume text saved to your configured database.")
        with st.form("evidence_form"):
            evidence = st.text_area("Verified career evidence (projects, outcomes, metrics, challenges, STAR stories)",
                                    value=store.fetch("evidence"), height=330,
                                    help="Facts you can prove. Prompts treat these as VERIFIED FACTS.")
            interpretations = st.text_area("My interpretations and context (unverified)",
                                           value=store.fetch("interpretations"), height=160,
                                           help="How you see your impact, strengths or motivations. Prompts label "
                                                "anything drawn from here as INTERPRETATION, never as fact.")
            if st.form_submit_button("Save evidence"):
                store.save("evidence", evidence)
                store.save("interpretations", interpretations)
                st.success("Evidence saved")
        with st.expander("View saved resume text"):
            st.text_area("Master resume", value=store.fetch("resume"), height=250, disabled=True)

    with tab2:
        st.subheader("Tailor resume for a job")
        with st.form("resume_form"):
            job_title = st.text_input("Job title / organization")
            jd = st.text_area("Paste the job description", height=250)
            label = "Build resume prompt" if provider == "manual" else "Generate evidence-grounded resume"
            tailor = st.form_submit_button(label)
        if tailor:
            try:
                prompt = build_resume_prompt(store.fetch("resume"), store.fetch("evidence"), jd, job_title,
                                             store.fetch("interpretations"))
            except ValueError as exc:
                st.warning(str(exc))
            else:
                store.save("last_job", f"{job_title}\n{jd}")
                show_prompt("resume", prompt, provider)
        prompt_panel("resume")
        review_draft("resume", RESUME_CHECKLIST, "careerpilot-tailored-resume.docx", "Download editable Word draft")

    with tab3:
        st.subheader("Interview question & answer studio")
        with st.form("interview_form"):
            interview_job = st.text_area("Job description (optional: uses your last resume job if blank)", height=180)
            focus = st.text_input("Interview focus", value="Leadership, stakeholder management, AI delivery, conflict resolution")
            count = st.slider("Number of questions", 3, 12, 6)
            label = "Build interview prompt" if provider == "manual" else "Generate interview Q&A"
            generate = st.form_submit_button(label)
        if generate:
            try:
                prompt = build_interview_prompt(store.fetch("resume"), store.fetch("evidence"),
                                                interview_job.strip() or store.fetch("last_job"), focus, count,
                                                store.fetch("interpretations"))
            except ValueError as exc:
                st.warning(str(exc))
            else:
                show_prompt("interview", prompt, provider)
        prompt_panel("interview")
        review_draft("interview", INTERVIEW_CHECKLIST, "careerpilot-interview-prep.docx", "Download interview prep (Word)")

    with tab4:
        st.subheader("Job tracking")
        st.info("This first release supports manual tracking. Automated job discovery and submissions are deliberately separate follow-on modules.")
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

    st.divider()
    with st.expander("Data management"):
        st.markdown("**Private export**: download everything saved in the database (resume text, evidence, "
                    "interpretations, last job, job history). Store the file somewhere private.")
        if st.button("Prepare full data export"):
            st.session_state["export_json"] = json.dumps(store.export_all(), indent=2)
        if st.session_state.get("export_json"):
            st.download_button("Download data export (JSON)", st.session_state["export_json"],
                               file_name="careerpilot-export.json", mime="application/json",
                               on_click=lambda: st.session_state.pop("export_json", None))
        st.warning("These actions permanently remove the selected records from the active database, but not necessarily provider backups.")
        if st.button("Delete saved resume and career evidence"):
            for key in ("resume", "evidence", "interpretations"):
                store.delete(key)
            st.success("Current database records deleted")
        if st.button("Delete job history"):
            for key in ("tracked_jobs", "last_job"):
                store.delete(key)
            st.success("Job history deleted")


try:
    render()
except StorageError as exc:
    st.error(exc.hint)
    if st.button("Retry"):
        st.rerun()
