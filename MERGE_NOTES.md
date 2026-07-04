# CMS fork ⇄ upstream merge (branch `merge-cms-dev-main`)

## Goal
Move the Austrian Olympiad (AOI) CMS fork onto **cms-dev/main** so the merge
base advances to today and future upstream pulls are cheap. We are merging
`github/main` (cms-dev) into our `live` history and resolving conflicts so the
result = **upstream main + AOI custom features**.

Merge base before this work: `0401c533` (2021-12-20). After this merge commit,
the base becomes current `main` (`101e372c`).

## Standing decisions (already made by the maintainer)
1. **Keep our isolate fork (arm64).** Upstream deleted the `isolate` submodule
   and `apt install`s isolate (amd64-only). We KEEP the submodule
   (`isolate` gitlink → `335eee5`, `.gitmodules` → austrian-olympiad-informatics/cms-isolate)
   and keep building it from source in the Dockerfile, so the dev env works on
   Apple Silicon.
2. **Migrate config to TOML.** Upstream replaced JSON `cms.conf` with
   `cms.toml` (`cms/conf.py` uses `tomllib`). Adopt upstream TOML. AOI config
   additions become TOML keys. The `aoi-portal` repo must also switch its
   mounted `docker/cms.docker.conf` (JSON) → a `.toml` file + compose update.
3. **Adopt upstream packaging** (`pyproject.toml` + `install.py` +
   `constraints.txt`, Python 3.11). Drop `requirements.txt`, `dev-requirements.txt`,
   `setup.py`. Port AOI-only deps into `pyproject.toml`.

## AOI-only deps to preserve in pyproject.toml
- `PyNaCl` — used by `cms/server/contest/authentication.py` (portal SSO / session tokens).
- `secure-cookie` — DROP. Upstream replaced it with tornado `create_signed_value`.
- `colorlog` — DROP. Not imported anywhere in the tree.
- Everything else our old requirements.txt pinned is already in upstream pyproject.

## Resolution principle for conflicts
- Prefer **upstream's structure/refactor**; re-apply the AOI *feature* on top.
- AOI feature areas to preserve (grep for them on the HEAD side of markers):
  frontend-v2 + CMS SSO/session tokens, subtask-scores-in-DB, extra languages
  & AOI task types (output-only / communication / codegolf), meme system,
  Discord webhook bot, StupidSandbox (arm64 no-isolate fallback).
- After resolving a file, remove ALL conflict markers (`<<<<<<<`, `=======`,
  `>>>>>>>`) and make sure it parses.

## Conflict ownership
### Claude (semantic / high-risk) — do NOT delegate
packaging: pyproject.toml, setup.py, requirements.txt, dev-requirements.txt,
Dockerfile, .gitmodules, isolate; config: cms/conf.py, config/cms.conf.sample→toml;
db models: cms/db/*; services: EvaluationService, ScoringService, esoperations,
scoringoperations; auth/SSO: cms/server/{admin,contest}/authentication.py,
cmscommon/crypto.py; plus Sandbox.py, Job.py, ParameterTypes.py,
steps/evaluation.py, jinja2_toolbox.py, handlers, dataset.py, CleanFiles.py.

### Delegated (mechanical) — safe for cheap subagents
.gitignore, .dockerignore, config/.gitignore (union both sides);
.github/dependabot.yml, .github/workflows/main.yml (take upstream/main);
cms/server/admin/templates/{participation,questions}.html (keep upstream layout +
re-add AOI rows/fields); cms/grading/languages/{cpp20_gpp,python3_pypy}.py
(additive language tweaks — keep upstream base, re-apply AOI flag/behavior).

## Status log
- [x] Merge started, 45 conflicts.
- [x] Structural/packaging: setup.py (kept Discord/Ojuz/Brainfuck entrypoints),
      pyproject.toml (added PyNaCl, dropped secure-cookie/colorlog), removed
      requirements.txt/dev-requirements.txt, gitignores/dockerignore/CI.
- [x] isolate: kept our submodule (arm64) + `.gitmodules`.
- [x] config loader: took upstream `cms/conf.py` (TOML); removed JSON
      `config/cms.conf.sample`.
- [x] Dockerfile: **upstream's Dockerfile** (ubuntu:noble, install.py, cmsuser,
      TOML) kept intact, with only the `apt install isolate` swapped for an
      isolate-builder stage that compiles our arm64 fork from source. NOTE: this
      changes the image contract (single dev image, `cmsuser`, config under
      `~/cms/etc/*.toml`, `install.py` venv) — aoi-portal's docker-compose +
      service commands + config mounts must be reworked to match (see below).
- [x] db models (7 files) — kept AOI columns/models (UserEval, PrintJob, Meme,
      SubtaskScore, session tokens) + upstream annotations/Group/get_allowed_languages.
- [x] handlers/templates/languages/misc glue (14 files).
- [x] crypto.py (constant-time compare preserved).
- [x] services (subtask-scores/user-eval) + auth/SSO + Sandbox (StupidSandbox
      arm64 fallback) + jinja2 (8 files).
- [x] All 45 conflicts resolved; whole tree byte-compiles.

## NEEDS REVIEW (semantic judgment calls made during the merge)
- **contest/authentication.py**: admin impersonation was reconciled by adding a
  third session-token source `SESSION_TOKEN_SOURCE_ADMIN_IMPERSONATION` (defined
  in cms/db/user.py + added to the `session_token_source` Enum → needs a DB
  migration). Verify this matches upstream's impersonation flow end-to-end.
- **Sandbox.py**: `StupidSandbox.__init__(name, temp_dir)` signature changed
  (dropped `file_cacher`) to match upstream's `Sandbox`. Any
  `StupidSandbox(file_cacher, ...)` call sites must drop that arg.
- **steps/evaluation.py**: AOI's `stderr_to_stdout` user-eval hack was dropped
  by the merge — re-add if the "user eval shows stderr" feature is still wanted.
- Config keys accessed via `getattr(config, ..., default)` in Sandbox.py fail
  soft until added to cms.toml (see remaining-work item 1).

## Remaining work (post-merge, NOT done yet — needs care + testing)
1. **Config TOML key re-addition.** We took upstream `conf.py` wholesale.
   Upstream nested config into sections (`web_server`, `contest_web_server`,
   `admin_web_server`, `sandbox`, `worker`, `proxy_service`, `telegram_bot`,
   `prometheus`, `global_`). AOI-only keys the merged code still references at
   top level and that upstream's `Config` LACKS — must be re-added to the
   dataclass + `config/cms.sample.toml`, or the call sites updated to the new
   nested location:
     - `config.memes_path`  (meme system)
     - `config.chroot_base_image`  (external sandbox base images)
     - `config.seccomp_enabled`, `config.apparmor_enabled`, `config.use_cgroups`
       (AOI reads these at top level; upstream moved sandbox opts under
       `config.sandbox.*` — reconcile)
     - `config.admin_cookie_duration` → upstream `config.admin_web_server.cookie_duration`
     - Discord webhooks / SSO / session-token settings used by the Discord bot
       and portal SSO handlers (grep `discord`, `sso`, `session_token`).
2. **aoi-portal repo (cross-repo).** Convert `docker/cms.docker.conf` (JSON) →
   `cms.docker.toml`; update `docker-compose.yml` mount path + the cms service
   commands for the new image/config path (`/usr/local/etc/cms.toml` or CMS_CONFIG).
3. **Review flags from subagents:** `steps/evaluation.py` dropped AOI's
   `stderr_to_stdout` (user-eval Batch hack) — re-add if still needed;
   `contestuser.py` dropped a `Task` import — verify.
4. **Build + run test on arm64** (whole docker stack) once 1–3 done.
5. Before finalizing, delete this MERGE_NOTES.md.
