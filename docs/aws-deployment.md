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

Set the API key in the environment for the one command that needs it. It is not
written to the repo.

```bash
cd deploy/aws
TIINGO_API_KEY=your_real_key_here ./deploy.sh
TIINGO_API_KEY=your_real_key_here ./create-function.sh
```

Order matters: `create-function.sh` points the function at an image that must
already be in ECR.

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

```bash
aws ce get-cost-and-usage \
  --time-period Start=$(date -u +%Y-%m-01),End=$(date -u +%Y-%m-%d) \
  --granularity MONTHLY \
  --metrics UnblendedCost
```

Expected: `0`. Lambda, the Function URL, S3, DynamoDB, SQS and 5 GB of
CloudWatch Logs are in AWS's always-free tier for every account, regardless of
plan or account age. ECR allows 500 MB of private storage; the image is about
211 MB, so it sits under that. Nothing here bills by the hour.

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
built for arm64 and the function is x86_64, or the reverse. `deploy.sh` passes
`--platform linux/amd64` to prevent this; if you build by hand, do the same.

**The function exits on every invoke.** Almost always a missing
`TIINGO_API_KEY`. `server.py` calls `check_setup()` at import time and refuses
to start without it, which is deliberate: failing at deploy time is easier to
debug than failing on the first screening request.

**403 from the Function URL.** The resource policy is missing. `auth-type NONE`
is not sufficient on its own; the URL also needs `FunctionURLAllowPublicAccess`
allowing `lambda:InvokeFunctionUrl`. `create-function.sh` adds it.

**Timeout on the first request after a quiet period.** A cold start. The image
is about 211 MB, so expect one to two seconds. Warm invocations are well under
one second.
