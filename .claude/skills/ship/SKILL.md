---
name: ship
description: Ship the current work through this repo's PR flow. `/ship` commits, pushes, opens a PR and watches CI, then stops. `/ship merge [PR#]` merges an approved PR and watches the prod deploy.
argument-hint: "[merge [PR#]]"
disable-model-invocation: true
---

# /ship

Two stages. Stage 1 never merges. Stage 2 runs only when the user typed
`/ship merge`, which is their approval to merge (CLAUDE.md rule: merge only
with approval). Never merge in stage 1, even if CI is green.

Follow the Commits section of CLAUDE.md for every commit, branch and PR.

## Stage 1: `/ship` (no arguments)

1. **Check state.** `git status --short`, `git branch --show-current`,
   `git log --oneline origin/main..HEAD`. If there is nothing to commit and no
   unpushed commits, say so and stop.
2. **Branch.** If on `main`, create `<type>/<short-description>` from the
   change. Never commit to `main`.
3. **Check locally** before committing, only for the kinds of files that changed:
   - Python: `uv run ruff check .`, `uv run ruff format --check .`, `uv run pytest -q`
   - dbt SQL: `cd dbt && uv run sqlfluff lint models`
   - Terraform: `terraform -chdir=terraform fmt -check` and `validate`
     (plan only if asked; never apply).
   Fix failures before committing. Report anything you could not fix.
4. **Commit.** One logical change per commit. Split infrastructure, dbt
   models, Python and docs into separate commits. Stage files by path, not
   with `git add -A`. End each message with the attribution lines from the
   system reminder.
5. **Push and open the PR.** `git push -u origin <branch>`, then
   `gh pr create` with a title that matches the main commit and a short body:
   what changed, why, and how it was checked locally. End the body with the
   PR attribution lines.
6. **Watch CI.** `pr.yml` and `deploy.yml` both skip changes that only touch
   `docs/**` or `**.md`. If every changed file
   (`git diff --name-only origin/main...HEAD`) matches, no CI runs: say so,
   skip to step 7, and skip the deploy watch in stage 2.
   Otherwise the run takes a few seconds to appear. Poll for it, then
   watch it:
   ```bash
   for i in $(seq 1 12); do id=$(gh run list --branch <branch> --json databaseId --jq '.[0].databaseId'); [ -n "$id" ] && break; sleep 15; done
   gh run watch "$id" --exit-status --interval 30 >/dev/null 2>&1; echo "exit=$?"
   gh pr checks <n> | cut -f1,2
   ```
   If a check fails, read its log (`gh run view "$id" --log-failed`), fix it,
   commit, push, and watch again. Stop after two failed fix attempts and
   report.
7. **Stop.** Report the PR link and each check's result, then say:
   "Run `/ship merge` to merge." Do not merge.

## Stage 2: `/ship merge [PR#]`

1. **Pick the PR.** Use the number given. If none was given, use the open PR
   for the current branch (`gh pr view --json number,state,headRefName`).
   If there is none, or more than one, ask.
2. **Check it is mergeable.** All checks must pass: `gh pr checks <n>`. If any
   check failed or is still pending, stop and report. Don't merge. A
   docs-only PR has no checks ("no checks reported"); that is expected.
   GitHub does not enforce checks on this repo (private, free plan: no
   branch protection), so this step is the only gate.
3. **Merge.** `gh pr merge <n> --merge --delete-branch`. Note the merge
   commit sha (`git log --oneline -1` on `main` after the fast-forward).
4. **Deploy.** `deploy.yml` skips pushes that only touch `docs/**` or
   `**.md`. Check the PR's files (`gh pr diff <n> --name-only`). If every file
   matches, say no deploy runs and stop.
   Otherwise, find the run for the merge commit and watch it **in the
   background** (`run_in_background: true`). Deploys can take over 10
   minutes, past the foreground timeout:
   ```bash
   for i in $(seq 1 12); do id=$(gh run list --branch main --workflow deploy.yml --limit 3 --json databaseId,headSha --jq '.[] | select(.headSha | startswith("<sha>")) | .databaseId'); [ -n "$id" ] && break; sleep 15; done
   echo "run=$id"; gh run watch "$id" --exit-status --interval 30 >/dev/null 2>&1; echo "exit=$?"
   gh run view "$id" --json jobs --jq '.jobs[] | "\(.name)\t\(.conclusion)"'
   ```
   While it runs, tell the user it is deploying and that they can keep working.
5. **Report.** Each deploy job's result. If one failed, show
   `gh run view "$id" --log-failed | tail -50` and stop. Never rerun or roll
   back prod without approval.

## Always

- End with the numbered list of commands run and their outcomes (user
  preference).
- Never run `terraform apply`, delete commands, or prod writes from here.
  `deploy.yml` does the prod work.
