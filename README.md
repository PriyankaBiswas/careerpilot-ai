# CareerPilot AI — private single-user MVP

**Features:** upload and keep several PDF/DOCX/TXT master resumes; keep a library of evidence records (project, employer, role, period, ownership, outcome) plus your own interpretations, kept separate; **generate tailored resumes and STAR interview answers inside the app with the OpenAI API**; map every claim to its evidence and keep unsupported claims out of the approved resume; edit, regenerate single sections, save versions per job and download Word files; track jobs manually; export all your saved data.

**Hosting stays free:** Streamlit Community Cloud (app) + Supabase Free (PostgreSQL). **OpenAI API usage is billed by OpenAI**: the app shows a cost estimate before every call and the actual cost afterwards. `AI_PROVIDER = "manual"` remains available as a free copy/paste fallback.

**Scope:** A starter MVP, not a production-hardened multi-user product. AI drafts require your review. It does not browse LinkedIn, scrape websites, submit applications or run background agents. Uploaded documents are parsed in memory; extracted text is saved in PostgreSQL. Do not use employer-confidential or sensitive client data without authorization.

## 1. How it works

| Tab | What it does |
| --- | --- |
| Career evidence | Upload and name master resumes. Maintain **evidence records**: one row per project or STAR story, with an **Ownership** field (Individual, Team, Shared or Unspecified) and a **Verified** tick. Only verified rows are ever sent as evidence. Separately, write **interpretations** (your own framing, used for tone only). |
| Resume studio | Choose a master resume and paste a job description. Optionally run a low-cost job match first. Then **Generate tailored resume**, review the claim-to-evidence mapping, optionally run the AI evidence check, regenerate any one section, edit, **save a version for this job** and download a Word file. |
| Interview coach | Paste a job description or a single interview question. The app ranks your verified evidence (free keyword ranking, with optional AI ranking), you pick what to use, then **Generate STAR answers**. You get detailed STAR answers, a 60–90 second spoken version, follow-up questions and missing-evidence flags. |
| Job tracker | Track jobs manually and export them as JSON. |
| Data management | API usage log, full JSON export, and deletion of resumes, evidence, versions or job history. |

### Models

| Setting | Default | Used for |
| --- | --- | --- |
| `OPENAI_MODEL` | `gpt-6.1-terra` | Resume drafting, section regeneration, STAR answers |
| `OPENAI_FAST_MODEL` | `gpt-6-luna` | Job requirement extraction and matching, AI evidence check. Set `"none"` to use `OPENAI_MODEL` instead. |

Your API key must have access to both models (check the Limits page in the OpenAI dashboard). If it doesn't, the app says which model is unavailable, and you can point either setting at a model you can use. Both are reasoning models: `OPENAI_REASONING_EFFORT` (default `medium`) and `OPENAI_FAST_REASONING_EFFORT` (default `low`) trade quality against reasoning-token cost.

### Accuracy guardrails

Generation uses **Pydantic-validated structured outputs**, so every claim comes back with the source IDs it cites (`R1`… are resume lines, `E1`… are evidence records), quoted supporting phrases, a support label and an ownership label. The app then checks each claim **deterministically, in code rather than by a model**:

| Check | Effect |
| --- | --- |
| Cites no source, or a source ID that does not exist (including unverified records) | Excluded |
| A quoted phrase does not appear in the cited source | Excluded |
| A number, percentage or amount is not in the cited sources | Excluded |
| Claims personal ownership (`ownership=individual`, or starts "Led/Built/Owned…") of work recorded as **Team** | Excluded |
| The model itself labelled it `partial` or `unsupported` | Excluded |
| Employer, title or dates of a role don't match the master resume | Whole role excluded |
| Role order differs from the master resume | Warning |
| Skill, education, certification, contact line or headline not found in your sources | Left out |
| Spoken interview answer outside 150–225 words (60–90 seconds) | Flagged |

The **approved resume** contains only claims that pass every check. Excluded claims stay visible in the claim-to-evidence table with the reason. When you regenerate a role's bullets, its employer, title and dates are kept exactly as before. If you edit the approved text and add numbers that aren't in your sources, the app warns you. The Word download stays disabled until you tick the claim review checklist.

**AI evidence check.** This optional, separately-billed button asks the fast model to review each claim against the same sources. Any claim it marks as unsupported or partial is also excluded. **It is a second pass by a language model, not independent proof.** It can miss problems, and the deterministic checks only prove that claims trace back to your sources, not that your sources are true. You are the final verifier.

**Untrusted input.** Job descriptions, interview questions and uploaded documents are placed in tagged data blocks after the rules. Anything inside them that looks like one of the app's tags is neutralised, and the model is told to ignore instructions found there. Even if an injected instruction gets through, any claim it produces still has to pass the checks above.

### Cost controls

- **No call without a click.** Every paid call is its own button showing an estimate *before* calling: estimated input tokens, the max output tokens, and the worst-case cost. Nothing runs automatically. The SDK's automatic retries are disabled (`max_retries=0`), so a failure never leads to a hidden second paid call.
- **Cost guard.** If the worst-case estimate is above `OPENAI_MAX_COST_PER_OPERATION` (default $0.25), or above the lower limit you set in the sidebar, the button is disabled. A model with no known price needs an explicit confirmation.
- **Max output tokens** (default 16,000, which includes reasoning tokens) can be set in secrets and lowered or raised per session in the sidebar.
- **Actual cost** is calculated from the API's reported usage (input, cached, output and reasoning tokens). It's shown after each call, totalled per session in the sidebar, and kept in the API usage log (token counts and cost only, no content). If a call fails after being billed (for example it hit the token limit), its cost is still recorded.
- Default prices (USD per 1M tokens, OpenAI Standard tier, checked 2026-11-05): `gpt-6.1-terra` $1.00 input / $0.05 cached / $5.00 output; `gpt-6-luna` $0.10 / $0.01 / $0.50. Above 272K input tokens, long-context rates apply. If prices change, override them with `OPENAI_PRICES` (JSON).
- Typical cost: a full resume draft with Terra is about 5–15K input tokens plus a few thousand output tokens, so a few cents. Luna operations cost a fraction of a cent.

**API failures** are reported in plain language with nothing lost: exhausted credit or a billing limit (`insufficient_quota`), rate limits, an invalid key, no access to the model, timeouts, connection errors, server errors, output cut off at the token limit, refusals, and output that fails validation. Retry by clicking again.

### Manual mode (`AI_PROVIDER = "manual"`)

This is the copy/paste fallback from the previous release. It builds evidence-grounded prompts for ChatGPT or Claude, lets you paste the result back, and needs no API key or API billing.

## 2. Configuration reference

The app reads Streamlit secrets first, then environment variables. All keys go at the TOML root (no `[sections]`). See `.streamlit/secrets.toml.example`.

| Key | Required | Notes |
| --- | --- | --- |
| `DATABASE_URL` | Yes | PostgreSQL URI. SSL is required automatically for non-local hosts (`sslmode=require`), unless the URI sets its own `sslmode`. |
| `AI_PROVIDER` | No | `openai` (default) or `manual`. **If your existing secrets say `AI_PROVIDER = "manual"`, change it or remove the line.** |
| `OPENAI_API_KEY` | For `openai` | From platform.openai.com > API keys. Needs API billing or credit. |
| `OPENAI_MODEL` / `OPENAI_FAST_MODEL` | No | Defaults `gpt-6.1-terra` / `gpt-6-luna`. |
| `OPENAI_MAX_OUTPUT_TOKENS` | No | Default `16000` (range 1,000–128,000). |
| `OPENAI_MAX_COST_PER_OPERATION` | No | Default `0.25` (USD). It's the hard ceiling; the sidebar can only lower it. |
| `OPENAI_REASONING_EFFORT` / `OPENAI_FAST_REASONING_EFFORT` | No | `none`, `low`, `medium`, `high`, `xhigh` or `max`. Defaults `medium` / `low`. |
| `OPENAI_PRICES` | No | JSON price overrides per model (USD per 1M tokens): `input`, `cached_input`, `output`, and optionally `long_input`, `long_cached_input`, `long_output`. |
| `APP_PASSWORD` | No | Optional second layer of defense. If you set it, it must be 16+ characters. **Required in practice for any publicly reachable Docker host.** |
| `DATABASE_SSLMODE` | No | Overrides the SSL mode, e.g. `verify-full`. |

### Upgrading an existing deployment

1. In Streamlit Cloud > App settings > Secrets, add `OPENAI_API_KEY`. Set `AI_PROVIDER = "openai"`, or delete the line.
2. Your existing data is migrated automatically on first use. The single saved resume becomes "Master resume". Each paragraph of the old evidence text becomes an evidence record (`E1`, `E2`, …), marked Verified with Ownership "Unspecified". **Review those records**, split them up where useful, and set Ownership: that's what stops team work being presented as yours.
3. The original keys are kept, so an older export remains valid.

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
   AI_PROVIDER = "openai"
   OPENAI_API_KEY = "sk-..."
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

**OpenAI API**
- Not free: billed per token against your OpenAI credit. The cost guard and the per-call estimates are there to keep spending visible and bounded.

**Manual AI mode**
- You copy and paste prompts by hand. Very long prompts (over about 100,000 characters) trigger a warning because chat apps may truncate them. Usage counts against your ChatGPT Plus or Claude Pro message limits.

Limits change over time. Check https://docs.streamlit.io/deploy/streamlit-community-cloud and https://supabase.com/pricing.

## 7. Privacy and data handling

- The app logs no career content. Error messages never show the database URL or credentials, and Python tracebacks are hidden in the browser (`client.showErrorDetails = false`).
- **Data export:** Data management > Prepare full data export downloads master resumes, evidence records, interpretations, saved resume versions, last job, job history and the API usage log as `careerpilot-export.json`. The file name is git-ignored; store it privately.
- **Deletion:** Data management can delete resume, evidence and interpretations, or the job history. Provider backups may still hold copies.
- In OpenAI mode, the selected resume, verified evidence, interpretations and job description are sent to the OpenAI API with `store=False`, so responses are not stored for later retrieval. Check OpenAI's API data-retention policy. Unverified evidence records are never sent.
- Application logs contain only operation names, model names, token counts, costs and error categories. They never contain prompts, outputs, resume text or the API key.
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

The tests use only **synthetic data** (a fictional candidate and companies) and a **fake OpenAI client**. They never call the real API or need a key. They cover the structured schemas, every grounding check (fabricated metrics, invented quotes, unknown sources, team work claimed as individual, altered dates, injected tags), cost estimates and the guard, API error handling (including exhausted credit), and that logs contain no content. End-to-end app tests check that no call happens without a click, that the cost guard blocks calls, and that unsupported claims never reach the approved resume or saved versions.

To run the app locally, create `.streamlit/secrets.toml` from the example (it's git-ignored), then run `.venv/bin/streamlit run app/main.py`.

GitHub Actions (`.github/workflows/ci.yml`) compiles the app and runs the full test suite against a throwaway PostgreSQL container. No secrets are used.

### Optional: Docker (portable, any host)

1. Copy `.env.example` to `.env` (git-ignored), set `APP_PASSWORD` and `OPENAI_API_KEY` (or set `AI_PROVIDER=manual`).
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
- Word export uses a single, simple ATS-friendly layout (headings and bullets), with no templates.
- Claim checks are string-based. Paraphrased quotes or reformatted dates can be flagged as unsupported, and an accurate claim can occasionally be excluded; regenerate the section or edit the text.
- Single key/value table with no migrations framework.

## References

- https://docs.streamlit.io/deploy/streamlit-community-cloud/share-your-app
- https://docs.streamlit.io/deploy/streamlit-community-cloud/manage-your-app
- https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management
- https://supabase.com/docs/guides/database/connecting-to-postgres
- https://supabase.com/pricing
- https://developers.openai.com/api/docs/pricing
- https://developers.openai.com/api/docs/models/gpt-6.1-terra
- https://developers.openai.com/api/docs/models/gpt-6-luna
