# CareerPilot AI — private single-user MVP

**Features:** upload PDF/DOCX/TXT resume; save verified experience evidence (plus your own interpretations, kept separate); build evidence-grounded resume and interview prompts; paste AI results back, review claims and export editable Word documents; manually track job applications; export all your saved data.

**Default cost: $0.** The recommended setup is Streamlit Community Cloud (hosting) + Supabase Free (PostgreSQL) + `AI_PROVIDER = "manual"`, where you copy prompts into the ChatGPT Plus or Claude Pro subscription you already have. No API key or API billing is needed.

**Scope:** A starter MVP, not a production-hardened multi-user product. AI drafts require review. It does not browse LinkedIn, scrape websites, submit applications or run background agents. Uploaded documents are parsed in memory; extracted text is saved in PostgreSQL. Generated drafts persist only in the current browser session until downloaded. Do not use employer-confidential or sensitive client data without authorization.

## 1. How it works

| Tab | What it does |
| --- | --- |
| Career evidence | Upload your master resume. Save **verified evidence** (facts you can prove) and, separately, **interpretations** (your own framing, which is always treated as unverified). |
| Resume studio | Paste a job description and get a complete prompt. Copy it into ChatGPT or Claude, paste the answer back, edit it, work through the claim review checklist, then download a Word file. |
| Interview coach | The same flow for STAR interview Q&A, with follow-ups, 30-second versions and evidence gaps. |
| Job tracker | Track jobs manually and export them as JSON. |
| Data management | Export everything saved (JSON) or delete records. |

**Anti-fabrication guardrails** (same in manual and API mode):

- Candidate facts may come only from the saved resume and verified evidence. The job description is treated as untrusted data.
- The AI must output a **claim ledger**: every claim is labelled VERIFIED FACT, INTERPRETATION or SUGGESTION [VERIFY], with its source quoted. Gaps are listed, not filled.
- The app flags any `[VERIFY]` markers left in your edited draft. The Word download stays disabled until you tick every item on the claim review checklist.

### AI modes (`AI_PROVIDER`)

| Value | Behaviour | Needs |
| --- | --- | --- |
| `manual` (default) | Builds the prompt for you to copy and paste the result back | Nothing extra |
| `openai` (opt-in) | Sends the same prompt to the OpenAI Responses API and loads the result for review. The prompt stays visible as a fallback. | `OPENAI_API_KEY` and separate OpenAI API billing. `OPENAI_MODEL` is optional (default `gpt-4.1-mini`). |

Tips for manual mode: start a **new chat** for each prompt. If you would rather your career data not be used for model training, check the data controls in your ChatGPT or Claude settings.

## 2. Configuration reference

The app reads Streamlit secrets first, then environment variables.

| Key | Required | Notes |
| --- | --- | --- |
| `DATABASE_URL` | Yes | PostgreSQL URI. SSL is required automatically for non-local hosts (`sslmode=require`), unless the URI sets its own `sslmode`. |
| `AI_PROVIDER` | No | `manual` (default) or `openai`. |
| `APP_PASSWORD` | No | Optional second layer of defense. If you set it, it must be 16+ characters. **Required in practice for any publicly reachable Docker host.** |
| `OPENAI_API_KEY` | Only for `openai` | Never needed in manual mode. |
| `OPENAI_MODEL` | No | Default `gpt-4.1-mini`. |
| `DATABASE_SSLMODE` | No | Overrides the SSL mode, e.g. `verify-full`. |

## 3. Keep the GitHub repository private

1. The repository must stay **Private** (GitHub > Settings > General > Danger Zone shows the current visibility).
2. Never commit `.env`, `.streamlit/secrets.toml`, resumes, exports or generated Word files. `.gitignore` blocks `*.pdf`, `*.docx`, `*.doc`, `careerpilot-*.json`, `resumes/`, `exports/`, `private_data/`, `.env*` and `.streamlit/secrets.toml`.
3. Before each commit, run `git status` and check that nothing personal is staged.

## 4. Create the Supabase Free database

1. Sign in at https://supabase.com/dashboard and create a **New project** on the Free plan. Choose a region close to you and a strong database password. Save that password in your password manager.
2. When the project is ready, click **Connect** at the top of the project dashboard.
3. Copy the **Session pooler** connection string. Do **not** use the "Direct connection" string: on the Free plan the direct host is IPv6-only, and Streamlit Community Cloud needs IPv4. It looks like:
   ```
   postgresql://postgres.<PROJECT-REF>:<PASSWORD>@aws-0-<REGION>.pooler.supabase.com:5432/postgres
   ```
   - The username is `postgres.<PROJECT-REF>`, not plain `postgres`.
   - **URL-encode** special characters in the password (`@` → `%40`, `#` → `%23`, `/` → `%2F`, `:` → `%3A`, `%` → `%25`). The easiest fix is a password made only of letters and numbers.
   - Append `?sslmode=require`. The app also enforces SSL for non-local hosts.
   - The transaction pooler (port `6543`) also works, because the app disables prepared statements, but the session pooler is recommended.
4. You don't need to create any tables. On first start the app creates the `records` table and turns on **row level security**. That blocks access through Supabase's public Data API while the app's own database role keeps access. Don't add RLS policies for `records`.

## 5. Deploy on Streamlit Community Cloud

1. Push this repository to your **private** GitHub repository (see section 9).
2. Go to https://share.streamlit.io, sign in with GitHub, and authorize Streamlit to access private repositories when asked.
3. Click **Create app** and choose to deploy from a GitHub repository. Apps from private repositories are private by default. Set:
   - Repository: `YOUR_USERNAME/careerpilot-ai`
   - Branch: `main`
   - Main file path: `app/main.py`
   - Under **Advanced settings**, choose Python **3.12**.
4. In **Advanced settings > Secrets**, paste the contents of `.streamlit/secrets.toml.example` and fill in real values:
   ```toml
   DATABASE_URL = "postgresql://postgres.<PROJECT-REF>:<URL-ENCODED-PASSWORD>@aws-0-<REGION>.pooler.supabase.com:5432/postgres?sslmode=require"
   AI_PROVIDER = "manual"
   # APP_PASSWORD = "optional-16+-character-password"
   ```
5. Click **Deploy**. Streamlit installs `requirements.txt`, reads `.streamlit/config.toml` (upload limit, hidden error tracebacks) and runs `app/main.py`.
6. **Lock down viewers:** open the app's **Share** button (or **App settings > Sharing**). Confirm the app is **private** ("Only specific people can view this app") and invite only your own email address. Viewers sign in with Google or a one-time emailed link.
7. Optional: add `APP_PASSWORD` in Secrets for a second layer of protection. If it's not set, the sidebar notes that access depends on the viewer controls.
8. To change secrets later, go to **App settings > Secrets**. The app restarts automatically.

### Troubleshooting the database connection

Open **Manage app** (bottom right of the deployed app) to see the logs. At startup the app logs three value-free lines:

```
database_config  setting=DATABASE_URL source=secrets secrets_file=ok found_in_nested_section=False
database_profile host_kind=supabase_session_pooler port=5432 user_has_project_ref=True has_password=True placeholder_detected=False ...
database_error   operation=connect category=authentication exc_type=OperationalError sqlstate=none
```

These lines never contain the URL, user, password, host or any career data. The UI shows the same `category`.

| Log shows | Fix |
| --- | --- |
| `source=missing`, `secrets_file=missing` | Add `DATABASE_URL` under App settings > Secrets. |
| `secrets_file=invalid` | Secrets aren't valid TOML. The value must be quoted: `DATABASE_URL = "postgresql://..."`. |
| `found_in_nested_section=True` | Move `DATABASE_URL` above any `[section]` header so it sits at the root of the TOML. |
| `placeholder_detected=True` | Replace `[YOUR-PASSWORD]` with the real password, without the brackets. |
| `host_kind=supabase_direct_ipv6_only` | Switch to the **Session pooler** string (Supabase > Connect). |
| `category=authentication` | The user must be `postgres.<project-ref>` (`user_has_project_ref=True`). Check or reset the database password and URL-encode special characters. |
| `category=configuration` | The URL is malformed (often an unencoded `@`, `#` or `/` in the password). |
| `category=dns` | Wrong host. Copy it again from Supabase > Connect. |
| `category=network` or `timeout` | The project is paused (restore it in Supabase), or you're using the IPv6 direct host. |
| `category=ssl` | Keep `sslmode=require`. Don't set `DATABASE_SSLMODE=disable`. |

## 6. Free-tier limitations

**Streamlit Community Cloud**
- Only **one private app** per account. Apps from private repositories are private by default.
- Apps **sleep after 12 hours without traffic**. Waking one takes a short while; your data in Supabase is unaffected.
- Shared, limited resources (Streamlit documents roughly 0.078–2 CPU cores and 690 MB–2.7 GB memory, subject to change). Fine for one user.
- Viewer access control is per email address. There are no audit logs or SSO policies.

**Supabase Free**
- 500 MB database per project; at most 2 active free projects.
- Projects **pause after about 7 days of inactivity**. The app then shows "Cannot reach the database… the project may be paused". Restore it from the Supabase dashboard (your data is kept), then click **Retry** in the app.
- Treat Free as having **no backups you can rely on**. Use **Data management > Prepare full data export** regularly and keep the JSON somewhere private.

**Manual AI mode**
- You copy and paste prompts by hand. Very long prompts (over about 100,000 characters) trigger a warning because chat apps may truncate them. Usage counts against your ChatGPT Plus or Claude Pro message limits.

Limits change over time. Check https://docs.streamlit.io/deploy/streamlit-community-cloud and https://supabase.com/pricing.

## 7. Privacy and data handling

- The app logs no career content. Error messages never show the database URL or credentials, and Python tracebacks are hidden in the browser (`client.showErrorDetails = false`).
- **Data export:** Data management > Prepare full data export downloads resume text, evidence, interpretations, last job and job history as `careerpilot-export.json`. The file name is git-ignored; store it privately.
- **Deletion:** Data management can delete resume, evidence and interpretations, or the job history. Provider backups may still hold copies.
- In manual mode your prompt contains your resume and evidence. Only paste it into an AI service you are comfortable sharing that data with.

## 8. Local development and tests

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest -q                       # unit tests; PostgreSQL tests are skipped
createdb careerpilot_test                 # optional: local Postgres integration tests
TEST_DATABASE_URL=postgresql://localhost:5432/careerpilot_test .venv/bin/pytest -q
```

`TEST_DATABASE_URL` tests **drop and recreate** the `records` table. Never point it at your real database.

To run the app locally, create `.streamlit/secrets.toml` from the example (it's git-ignored), then run `.venv/bin/streamlit run app/main.py`.

GitHub Actions (`.github/workflows/ci.yml`) compiles the app and runs the full test suite against a throwaway PostgreSQL container. No secrets are used.

### Optional: Docker (portable, any host)

1. Copy `.env.example` to `.env` (git-ignored) and set `APP_PASSWORD`. Leave `AI_PROVIDER=manual` unless you want the API.
2. Run `docker compose up --build`, then visit http://localhost:8501. Compose provides a local Postgres and overrides `DATABASE_URL`.
3. Stop with `docker compose down` (keeps data) or `docker compose down -v` (deletes local data).

Any Docker host (Render, Fly.io, Cloud Run, etc.) can run the image with the same variables plus `PORT`. Those hosts make the app publicly reachable, so **always set `APP_PASSWORD`** there. Note that they may cost money.

## 9. Pushing changes

```bash
git status            # confirm no resumes, exports or secrets are staged
git add .
git commit -m "Describe my change"
git push
```

Streamlit Community Cloud redeploys automatically from `main`.

## 10. Current feature gaps

- No automated job discovery, LinkedIn scraping, application submission or background agents (deliberately out of scope).
- The optional password gate is not enterprise-grade auth: there's no rate limiting or password reset. Rely on Streamlit's private-app viewer controls.
- PDF extraction handles selectable text only; image-only PDFs need OCR.
- Word export is basic plain paragraphs.
- Single key/value table with no migrations framework.

## References

- https://docs.streamlit.io/deploy/streamlit-community-cloud/share-your-app
- https://docs.streamlit.io/deploy/streamlit-community-cloud/manage-your-app
- https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management
- https://supabase.com/docs/guides/database/connecting-to-postgres
- https://supabase.com/pricing
