# Security and CI/CD

Who can write where, how GitHub Actions authenticates without keys, and what runs on a pull request and on a merge.

## Identities

| Identity | Can write | Used by |
|---|---|---|
| `sa-ingestion-prod` | prod lake bucket, `bronze`, `ops` | the ingestion job |
| `sa-dbt-prod` | `silver`, `gold`, `ops` (reads `bronze`) | the dbt job |
| `sa-orchestrator-prod` | nothing; can only start the two jobs | the workflow and its schedule |
| `sa-dev` | dev lake bucket, `dev_*` datasets | local work, by impersonation |
| `sa-ci-pr` | `dev_*` datasets; reads the project and the Terraform state | pull request CI, any branch |
| `sa-ci-deploy` | infrastructure: Terraform, images, jobs | deploy CI, `main` only |

Local sessions impersonate `sa-dev`, so a local run cannot touch prod: reading the prod bucket or `bronze` as `sa-dev` returns 403. Prod data is written only inside Cloud Run, by the job accounts.

## Workload Identity Federation

There are no service account keys anywhere. Each workflow run gets a short-lived OIDC token from GitHub (a signed statement of which repo, branch, and event it is), and Google's STS (Security Token Service) exchanges it for a GCP token. The pool's provider accepts tokens only from this repo's numeric id, which, unlike its name, can never be reused by another repo. Defined in [`terraform/ci.tf`](../terraform/ci.tf).

Two CI accounts, so a pull request can never deploy, even if a branch edits the workflow file. A guardrail job on every PR (`deploy-sa-denied`) asks for a `sa-ci-deploy` token and passes only if Google refuses it.

The deploy binding matches a mapped attribute, `<repo id>:<git ref>`, not the OIDC subject. The first deploy failed because GitHub sent this repo's subject in its newer immutable form (`repo:<owner>@<id>/<name>@<id>:ref:...`), not the classic `repo:<owner>/<name>:ref:...` the binding expected. The attribute does not depend on that format.

## Pull requests ([`pr.yml`](../.github/workflows/pr.yml))

| Job | What it checks |
|---|---|
| `lint` | ruff, ruff format, SQLFluff (DuckDB templater, no credentials or data needed), `terraform fmt` |
| `test` | pytest: Pandera contracts and ingestion. Tests that need the Olist CSVs skip themselves |
| `terraform-plan` | `validate` and `plan` with `-lock=false`, since `sa-ci-pr` can only read the state |
| `dbt-dev` | `dbt build --target dev`; one at a time across PRs, since they share the dev datasets |
| `docker-build` | both images build (no push), to catch a broken Dockerfile before it reaches main |
| `deploy-sa-denied` | the guardrail above |

The plan output stays in the job log, where GitHub masks the secrets (project id, emails). There is no PR comment with the plan, since comments are not masked. Every job references the project id secret, because GitHub masks only the secrets a job uses, and the auth action exports the project id. The first run printed it in one job; that run was deleted and the workflow fixed.

## Merge to main ([`deploy.yml`](../.github/workflows/deploy.yml))

1. `terraform-apply`: plan, then apply that plan. Merging the PR is the approval.
2. `images`: build and push `ingestion` and `dbt`, tagged with the commit SHA. CI is the only thing that pushes images.
3. `deploy-jobs`: `gcloud run jobs update --image`, which changes only the image.
4. `prod-dbt`: `gcloud run jobs execute dbt --wait`. It runs as `sa-dbt-prod` like the scheduled pipeline, so prod writes and the `ops.pipeline_runs` rows come from the job, not from CI.

Doc-only changes (`docs/**`, `*.md`) skip the deploy. Deploys queue and never cancel each other.

Terraform owns every job setting except the image. The jobs have `lifecycle { ignore_changes }` on the image and on the `client` fields gcloud stamps, so a deploy never shows up as drift. The image variables still gate the jobs' creation on a fresh bootstrap; CI passes the images the jobs run now.

## Bootstrap

CI cannot authenticate before Workload Identity Federation exists, so the first apply of `ci.tf` ran locally. The repo secrets (`GCP_PROJECT_ID`, `TF_STATE_BUCKET`, `WIF_PROVIDER`, `CI_PR_SA`, `CI_DEPLOY_SA`, `DEVELOPER_EMAIL`, `ALERT_EMAIL`) were set with `gh secret set`, piped from `terraform output` so they never printed.

## Caveats, stated plainly

- The project owner's own credentials can still write to prod. Impersonation is a convention for local work; IAM is the real barrier for the jobs, CI, and the agent.
- `sa-ci-deploy` has an explicit role list, not Owner, but it includes `resourcemanager.projectIamAdmin`, so it could grant itself more. The real protection is that only main can use it.
- `sa-ci-pr` has basic Viewer, which through the legacy dataset ACL can also read prod tables. Read-only, accepted here.
- GitHub's free plan has no branch protection on private repos, so passing checks cannot be required before a merge. Locally, the `no-commit-to-branch` pre-commit hook blocks commits to main.
- Enabling Cloud Run created the default compute service account with `roles/editor` on the project. Nothing here uses it, so that grant was removed by hand (`gcloud projects remove-iam-policy-binding`). Terraform cannot remove grants it did not create, so this lives outside the IaC. Enabling another Google API later can add it back, so recheck after enabling one (last checked after enabling STS: still removed).

## Result

- PR checks: all 7 pass; `terraform plan` shows no changes; `dbt build` on dev passes 207, warns 3, errors 0; the PR is refused a deploy token (`iam.serviceAccounts.getAccessToken denied`).
- Deploy run `37903021181` (2026-10-09): apply 0 changed, both jobs on images tagged `90cc8b4`, the dbt execution succeeded, and `ops.pipeline_runs` has its 20 model rows and a passing tests row.
- A local `terraform plan` afterwards shows no changes, so the image updates cause no drift.
