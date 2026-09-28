# Mizan on AWS Lambda: deployment design

Date: 2026-09-28
Status: implemented and deployed; blocked on verification criterion 3

## Goal

Run the existing Mizan backend and frontend on AWS, publicly reachable over
HTTPS, at zero ongoing cost, so that "AWS" can appear on the resume as a
deployed system rather than a course completion.

## Non-goals

- Replacing the Render deployment. Render stays live. Two URLs are fine and
  the Render one keeps working if AWS breaks.
- Splitting the frontend onto S3 and CloudFront. Considered and deferred as
  a later step, not part of this work.
- Any EC2, VPC, RDS, NAT Gateway or load balancer. None are needed, and
  several of them bill by the hour.
- Changing `server.py` or `index.html`. Not one line changes.

## Constraints

- The app is stateless. It writes nothing to disk and has no database, so it
  needs no persistent volume or network filesystem.
- Runtime dependencies are `flask`, `requests`, `gunicorn` only.
- The AWS account (676206919472) was created 2025-01-16 and had never finished
  activation. IAM and ECR answered; every other service returned
  `SubscriptionRequiredException` or `OptInRequired`. A payment method was
  added on 2026-09-28 and the account activated. No further action needed.
- Because the account predates 2025-07-15 it is not on the credit model, and
  its 12-month EC2 free window closed in January 2026. That does not matter
  here: Lambda, the Function URL, S3 and 5 GB of CloudWatch Logs are always
  free for every account regardless of age or plan.
- The user is in Malaysia. Region `ap-southeast-1` (Singapore).

## Architecture

```
Browser
  |
  | HTTPS
  v
Lambda Function URL  (public, auth type NONE)
  |
  v
Lambda function  (container image, 512 MB, 30 s timeout)
  |
  | Lambda Web Adapter extension runs gunicorn as a normal process
  v
gunicorn -> flask app (server.py)
  |
  +--> Tiingo API       (public internet, no VPC)
  +--> Yahoo Finance    (public internet, no VPC)
  +--> bundled local fallback figures (inside the image)
```

Because the function is not attached to a VPC, it reaches the internet
directly. That is deliberate: attaching a VPC would require a NAT Gateway to
restore egress, which costs about $32/month.

## Components

### `Dockerfile.lambda` (new)

A second Dockerfile. The existing `Dockerfile` is untouched and continues to
serve Render and `docker compose` locally.

- Base image `public.ecr.aws/lambda/python:3.11`.
- Lambda Web Adapter copied in from
  `public.ecr.aws/awsguru/aws-lambda-adapter:0.8.4` as an extension. The
  adapter is itself the Runtime Interface Client, so no exec wrapper is needed.
- `ENTRYPOINT` set to the gunicorn command. This is required rather than
  stylistic: the managed base image's own `ENTRYPOINT` is
  `/lambda-entrypoint.sh`, which requires exactly one argument and exits 142
  otherwise. The original design used `CMD` plus
  `AWS_LAMBDA_EXEC_WRAPPER=/opt/bootstrap`; that variable belongs to the Zip
  packaging flow, `/opt/bootstrap` does not exist in the adapter image, and the
  combination failed twice for two different reasons.
- The app listens on port 8080, which is the adapter's default. `server.py`
  already reads `PORT` from the environment, so this is configuration only.
- The image must be built and pushed with `--provenance=false --sbom=false`.
  Otherwise buildx wraps it in an OCI image index and Lambda rejects it.

### `deploy/aws/deploy.sh` (new)

Idempotent build-and-push script. Creates the ECR repository if absent,
builds the Lambda image, pushes it, and updates the function. Safe to re-run.

### `deploy/aws/create-function.sh` (new)

One-time setup: creates the Lambda function, its execution role, the
environment variables, and the Function URL with `NONE` auth.

### `docs/aws-deployment.md` (new)

How to redeploy, how to read logs, how to tear everything down.

### `.gitignore` (edit)

Ensure no credential files can be committed.

## Configuration

| Setting | Value | Why |
|---|---|---|
| Region | `ap-southeast-1` | Closest mature region to Malaysia |
| Memory | 512 MB | Ample; keeps GB-seconds low |
| Timeout | 30 s | Covers upstream retries with backoff |
| Architecture | `x86_64` | Built and verified on x86_64; arm64 was the original intent but not proven end to end, and an architecture mismatch fails as a confusing exec-format error |
| `PORT` | `8080` | Lambda Web Adapter default |
| `TIINGO_API_KEY` | from environment | Never written to the repo |
| Function URL auth | `NONE` | Must be public, same as Render now |

## Cost model

| Service | Free allowance | Expected use |
|---|---|---|
| Lambda requests | 1M / month | A few hundred |
| Lambda compute | 400,000 GB-s / month | Well under 1,000 |
| ECR storage | 500 MB / month | 237 MB of unique layers, measured |
| CloudWatch Logs | 5 GB / month | A few MB |
| Function URL | No charge | n/a |

Expected monthly cost: **$0**. A zero-spend budget alerts at $1 if that
stops being true.

The 237 MB figure was measured, not estimated. `aws ecr describe-images`
reports 248,519,624 bytes against the tag, but that is the size of the OCI
image index, which is the sum of its children rather than stored data. The
tagged object is an index with no layers; the amd64 manifest beneath it is
untagged and holds 11 layers totalling 248,514,456 bytes. ECR bills unique
layers, so 237 MB is the number that counts.

This leaves room for roughly one more image before the 500 MB allowance is
reached. It also means an ECR lifecycle rule must **not** be set to expire
untagged images: the manifest the `lambda` tag depends on is itself untagged,
so such a rule would delete it and break the tag.

## Verification

Nothing is claimed on the resume until all of these pass.

Deployed to `https://quai45qkwxlb5n6bodajvt3snm0tmgfe.lambda-url.ap-southeast-1.on.aws/`
on 2026-09-28.

| # | Criterion | Result |
|---|---|---|
| 1 | `curl <url>/health` returns `"ok":true` | **Pass**, 200 |
| 2 | `curl <url>/` returns the Mizan HTML page | **Pass**, 69,075 bytes, correct title |
| 3 | A real screening call returns correct figures | **Blocked**: needs the real `TIINGO_API_KEY`. Currently returns `Tiingo API error 403` |
| 4 | Warm invocation under 1 second | **Pass**, 61 to 78 ms over five calls |
| 5 | Cold start under 3 seconds | **Pass**, 1.75 s |
| 6 | Console shows the deployed image digest | **Pass**, `sha256:37f89cf5` |
| 7 | Cost for the current month is zero | **Not yet measurable**: Cost Explorer is not enabled on this account. Check in the Billing console instead |

Criterion 3 is the one that matters most and it is the one outstanding. A 403
from Tiingo means the placeholder key is in place. Until it is replaced, the
screening endpoint is not proven and the resume stays unchanged.

Criterion 7 cannot be satisfied by the command the original design named,
because that API requires activating Cost Explorer first. This is a limitation
of the check, not evidence of a charge. The budget alert is the practical
substitute and still needs an email address, which the account has not set.

## Risks

| Risk | Mitigation |
|---|---|
| Surprise bill | Zero-spend budget at $1, email alert |
| Access key leak | Scoped IAM user, key never in the repo or in chat |
| Cold start on the resume link | 1 to 2 s, still better than Render's 30 to 60 s wake |
| Account never activated | Blocker, not a risk. Resolve before any of this is reachable: add a payment method, or replace the account |
| Two deployments drift | Render keeps serving until AWS is verified, then the resume links to AWS |

## Rollback

Delete the Lambda function and the ECR repository. Nothing else in the
account depends on them. Render remains live throughout, so rollback is
"point the resume link back at Render".

## Out of scope, recorded for later

- Moving the frontend to S3 plus CloudFront for a wider AWS surface
- A GitHub Actions workflow that deploys on push to `main`
- A custom domain and an ACM certificate
