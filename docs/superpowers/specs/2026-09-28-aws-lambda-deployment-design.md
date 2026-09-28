# Mizan on AWS Lambda: deployment design

Date: 2026-09-28
Status: approved, pending implementation

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
- The AWS account (676206919472) was created 2025-01-16 and never finished
  activation. IAM and ECR answer; every other service returns
  `SubscriptionRequiredException` or `OptInRequired`. Lambda cannot be used
  until a payment method is added and activation completes. This is a
  prerequisite, not part of the build.
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

- Base image `public.ecr.aws/lambda/python:3.11`, which already contains the
  Lambda Runtime Interface Client.
- Lambda Web Adapter copied in from
  `public.ecr.aws/awsguru/aws-lambda-adapter:0.8.4` as an extension.
- `AWS_LAMBDA_EXEC_WRAPPER=/opt/bootstrap` so the adapter wraps the process.
- The app listens on port 8080, which is the adapter's default. `server.py`
  already reads `PORT` from the environment, so this is configuration only.

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

1. `curl <function-url>/health` returns `"ok":true`
2. `curl <function-url>/` returns the Mizan HTML page
3. A real screening call for a known symbol returns correct figures
4. Response time on a warm invocation is under 1 second
5. Cold start is under 3 seconds
6. The AWS console shows the function as the deployed image digest
7. `aws ce get-cost-and-usage` for the current month shows no charge

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
