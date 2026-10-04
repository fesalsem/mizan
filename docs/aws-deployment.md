# Deploying Mizan to AWS Lambda

Mizan runs in two places. Render serves the version linked from the resume
today. AWS Lambda is the second deployment, and it exists so that "AWS" is a
system that is actually running rather than a course that was completed.

Both are live at once. If AWS breaks, Render keeps answering.

## What actually runs

```
Browser
  | HTTPS
  v
Lambda Function URL   (public, auth NONE)
  |
  v
Lambda function       (container image, 512 MB, 30 s, x86_64)
  |
  | Lambda Web Adapter extension runs gunicorn as a child process
  v
gunicorn -> server.py (Flask)
  |
  +--> Tiingo API
  +--> Yahoo Finance
  +--> bundled fallback figures in the image
```

There is no VPC. Attaching one would remove internet access and require a NAT
Gateway at about $32 a month to get it back. The function needs to reach Tiingo
and Yahoo, so it stays outside a VPC.

`server.py` and `index.html` are identical in both deployments. Only the
container differs.

## The files

| File | Purpose |
|---|---|
| `Dockerfile.lambda` | The Lambda image. `Dockerfile` is untouched and still serves Render. |
| `deploy/aws/create-function.sh` | One-time setup: ECR, execution role, function, public URL. |
| `deploy/aws/deploy.sh` | Everyday build, push, and update. |
| `deploy/aws/lambda-trust-policy.json` | Lets Lambda assume the execution role. |

## First deployment

You need the AWS CLI authenticated as a principal that can create IAM roles,
ECR repositories, and Lambda functions. `aws login` is enough; no long-lived
access key is needed or wanted.

The first deploy has three steps, in this order. `deploy.sh` finishes with
`update-function-code`, so it assumes the function already exists;
`create-function.sh` points the function at an image that must already be in
ECR. Neither script can run first on its own, so the first pass creates the
repository, pushes the image, then creates the function.

```bash
cd deploy/aws

# 1. Create the ECR repository the image will be pushed to. create-function.sh
#    does this too, but it cannot get past the function step until an image
#    exists, so on the very first deploy create the repository by hand.
aws ecr create-repository --repository-name mizan --region ap-southeast-1 \
  --image-scanning-configuration scanOnPush=true

# 2. Build and push the first image. SKIP_UPDATE=1 because the function does
#    not exist yet, so there is nothing to update. deploy.sh never reads the
#    key.
SKIP_UPDATE=1 ./deploy.sh

# 3. Create the function, the public URL, and the two URL permissions. Only
#    this step needs the key.
TIINGO_API_KEY=your_real_key_here ./create-function.sh
```

After that, `./deploy.sh` on its own rebuilds, pushes, and updates the live
function, so the three-step dance is only ever needed once.

Only `create-function.sh` needs the key; `deploy.sh` builds and pushes and
never reads it. `create-function.sh` will also read the key from the repo's
`.env` if it is not in the environment, which is the easier route and keeps it
out of shell history.

If `create-function.sh` reports that the account cannot use Lambda, the account
is not fully activated. See Troubleshooting.

## Redeploying after a code change

```bash
cd deploy/aws
./deploy.sh
```

That builds, pushes, updates the function, waits for it to settle, and prints
the image digest that is now live. Run this after any change to `server.py`,
`index.html`, or `requirements.txt`.

To build and push without touching the live function:

```bash
SKIP_UPDATE=1 ./deploy.sh
```

## Changing configuration

Memory, timeout, and the API key are function settings, not image settings, so
changing them does not need a rebuild.

```bash
aws lambda update-function-configuration \
  --function-name mizan \
  --region ap-southeast-1 \
  --memory-size 512 \
  --timeout 30 \
  --environment "Variables={TIINGO_API_KEY=new_key,PORT=8080}"
```

The API key has to be updated here rather than in a `.env` file. `.env` is for
local `docker compose` only and is gitignored.

## Reading logs

```bash
aws logs tail /aws/lambda/mizan --follow --region ap-southeast-1
```

Drop `--follow` for a one-off look at recent output. A cold start logs a
`INIT_START` line, which is how you tell a slow first request from a slow app.

## Checking the bill

The Billing console is the reliable place to look. The API route needs
activating first:

```bash
aws ce get-cost-and-usage \
  --time-period Start=$(date -u +%Y-%m-01),End=$(date -u +%Y-%m-%d) \
  --granularity MONTHLY \
  --metrics UnblendedCost
```

This fails with `User not enabled for cost explorer access` until Cost Explorer
is turned on in the Billing console, which takes up to 24 hours and costs $0.01
per API request thereafter. Do not treat that error as a sign of a problem with
the deployment.

Expected: `0`. Lambda, the Function URL, S3, DynamoDB, SQS and 5 GB of
CloudWatch Logs are in AWS's always-free tier for every account, regardless of
plan or account age. ECR allows 500 MB of private storage and the image uses
237 MB of unique layers, so there is room for roughly one more image. Nothing
here bills by the hour.

## Tearing it down

```bash
aws lambda delete-function-url-config --function-name mizan --region ap-southeast-1
aws lambda delete-function --function-name mizan --region ap-southeast-1
aws ecr delete-repository --repository-name mizan --region ap-southeast-1 --force
aws iam detach-role-policy --role-name mizan-lambda-execution \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
aws iam delete-role --role-name mizan-lambda-execution
```

Nothing else in the account depends on these. Render keeps serving throughout,
so rollback is pointing the resume link back at Render.

## Troubleshooting

**`SubscriptionRequiredException` or `OptInRequired` on every service except
IAM and ECR.** The account never finished activation. This is an account-level
problem, not a Lambda problem. Add a payment method under Billing, or open an
account activation support case. A brand new account is the fallback.

**`The image manifest ... does not match the architecture`.** The image was
built for arm64 and the function is x86_64, or the reverse. `deploy.sh` derives
the buildx platform from `ARCH`, the same variable `create-function.sh` passes
to `--architectures` (default `x86_64`), so the image and the function agree.
Set `ARCH` the same for both scripts; if you build by hand, pass the matching
platform yourself.

**The function exits on every invoke.** Almost always a missing
`TIINGO_API_KEY`. `server.py` calls `check_setup()` at import time and refuses
to start without it, which is deliberate: failing at deploy time is easier to
debug than failing on the first screening request.

**403 from the Function URL.** A public URL needs **two** resource policy
statements, and `auth-type NONE` alone grants neither. One allows
`lambda:InvokeFunctionUrl` with `FunctionUrlAuthType NONE`; the other allows
`lambda:InvokeFunction` with `InvokedViaFunctionUrl true`. With only the first,
every request returns 403. `create-function.sh` adds both, and does so on every
run rather than only when the URL is first created, so re-running it repairs
statements a previous run failed to add.

The flags differ between the two, which is easy to get wrong:
`--function-url-auth-type` is rejected on `InvokeFunction` with "FunctionUrlAuthType
is only supported for lambda:InvokeFunctionUrl action". Use
`--invoked-via-function-url` there instead.

**Every invocation fails with `entrypoint requires the handler name to be the
first argument`, exit 142.** The container still has the AWS base image's
`ENTRYPOINT`, `/lambda-entrypoint.sh`, which requires exactly one argument. A
multi-argument `CMD` fails that check before the app starts. With a managed AWS
base image the adapter needs the `ENTRYPOINT` overridden to the web server
command, which is what `Dockerfile.lambda` does.

**The runtime exits 127 with `/opt/bootstrap: does not exist`.** That variable
belongs to the Zip packaging flow, not Docker. The adapter image ships only
`/lambda-adapter`, so `/opt/bootstrap` never exists. The adapter is itself the
Runtime Interface Client; no exec wrapper is needed for a container image.

**`create-function` fails with "The image manifest, config or layer media type
for the source image is not supported".** The tag points at an OCI image index.
Docker's buildx adds provenance and SBOM attestations by default, which wraps
the real manifest in an index, and Lambda accepts a manifest but not an index.
`deploy.sh` passes `--provenance=false --sbom=false` and then reads the media
type back and refuses to finish if it sees an index.

**Timeout on the first request after a quiet period.** A cold start. The image
is about 211 MB, so expect one to two seconds. Warm invocations are well under
one second.
