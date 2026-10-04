#!/usr/bin/env bash
#
# One-time setup for the Mizan Lambda function.
#
# Creates, if they do not already exist:
#   1. the ECR repository
#   2. the Lambda execution role and its logging policy
#   3. the Lambda function itself, from the image in ECR
#   4. a public Function URL
#
# Safe to re-run. Every step checks for the resource first and skips it if it
# is already there, so a failure halfway through does not require starting over.
#
# The image must already be in ECR. On the first deploy, create the repository
# and push the image first (SKIP_UPDATE=1 ./deploy.sh); docs/aws-deployment.md
# has the full first-deploy order.
#
# Usage:
#   TIINGO_API_KEY=xxx ./create-function.sh
#
# TIINGO_API_KEY is read from the environment. It is never written to disk and
# never committed. server.py calls check_setup() at import time, so the
# function will exit on every invocation without it.

set -euo pipefail

REGION="${AWS_REGION:-ap-southeast-1}"
FUNCTION_NAME="${FUNCTION_NAME:-mizan}"
ROLE_NAME="${ROLE_NAME:-mizan-lambda-execution}"
REPO_NAME="${REPO_NAME:-mizan}"
IMAGE_TAG="${IMAGE_TAG:-lambda}"
MEMORY_MB="${MEMORY_MB:-512}"
TIMEOUT_S="${TIMEOUT_S:-30}"
ARCH="${ARCH:-x86_64}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/../../.env"

# Prefer the environment, but fall back to the repo's .env, which is where the
# key already lives for docker compose and which is gitignored. The point is to
# avoid the key being pasted into a shell history or a chat log.
if [[ -z "${TIINGO_API_KEY:-}" && -f "${ENV_FILE}" ]]; then
  TIINGO_API_KEY="$(grep -E '^TIINGO_API_KEY=' "${ENV_FILE}" | head -1 | cut -d= -f2- | tr -d '"'"'"' \r')"
  export TIINGO_API_KEY
fi

# Match placeholders case-insensitively. Tutorial values like YOUR_TIINGO_KEY
# and REPLACE_ME are the ones people actually paste, and a case-sensitive match
# let them through to become the function's live key. Prefer a key exported
# from the environment or read from a secrets store over one passed on the
# command line, which lands in shell history; both routes still work here.
KEY_LOWER="$(printf '%s' "${TIINGO_API_KEY:-}" | tr '[:upper:]' '[:lower:]')"
case "${KEY_LOWER}" in
  "")
    echo "TIINGO_API_KEY is not set." >&2
    echo "Either export it, or put a real key in ${ENV_FILE}." >&2
    echo "Usage: TIINGO_API_KEY=xxx $0" >&2
    exit 1
    ;;
  *placeholder*|*replace*|*your_*|*your-*|*xxx*|*changeme*|*change_me*|*example*|*dummy*|*sample*|*todo*|*insert_*)
    echo "TIINGO_API_KEY still looks like a placeholder, not a real key." >&2
    echo "Put the real key in ${ENV_FILE}, or export a real one." >&2
    exit 1
    ;;
esac

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
IMAGE_URI="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${REPO_NAME}:${IMAGE_TAG}"
ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"

# Fail early and clearly if the account cannot use Lambda. This is the check
# that caught a half-activated account: IAM and ECR answered, everything else
# returned SubscriptionRequiredException.
echo "Checking Lambda access in ${REGION}..."
if ! aws lambda list-functions --max-items 1 --region "${REGION}" >/dev/null 2>&1; then
  echo "This AWS account cannot use Lambda yet." >&2
  aws lambda list-functions --max-items 1 --region "${REGION}" 2>&1 | tail -2 >&2
  echo "Add a payment method and complete account activation, then re-run." >&2
  exit 1
fi

echo "Account ${ACCOUNT_ID}, region ${REGION}"

echo "ECR repository ${REPO_NAME}..."
aws ecr describe-repositories --repository-names "${REPO_NAME}" --region "${REGION}" >/dev/null 2>&1 \
  || aws ecr create-repository --repository-name "${REPO_NAME}" --region "${REGION}" \
       --image-scanning-configuration scanOnPush=true >/dev/null
echo "  ok"

echo "Execution role ${ROLE_NAME}..."
if ! aws iam get-role --role-name "${ROLE_NAME}" >/dev/null 2>&1; then
  aws iam create-role \
    --role-name "${ROLE_NAME}" \
    --assume-role-policy-document "file://${SCRIPT_DIR}/lambda-trust-policy.json" \
    --description "Execution role for the Mizan Lambda function" >/dev/null
fi

# Attached unconditionally, outside the create branch. attach-role-policy is
# idempotent, and tying it to role creation meant a re-run after a failed
# attach skipped it, leaving the function unable to write logs, which makes
# every later debugging session harder.
#
# Logs only. The function reads no other AWS service, so it needs no other
# permission. Keeping the policy this narrow is the point of a separate role.
aws iam attach-role-policy \
  --role-name "${ROLE_NAME}" \
  --policy-arn "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
echo "  ok"

# IAM is eventually consistent. A role created seconds ago is often not yet
# assumable, and create-function then fails with "cannot be assumed by Lambda".
# get-role answers as soon as the role exists, which is a different condition,
# so polling it breaks on the first pass and never detects assumability. Wait
# for existence here; the real assumability wait is the bounded retry around
# create-function below, since that call is the one that needs the role.
aws iam wait role-exists --role-name "${ROLE_NAME}"

echo "Lambda function ${FUNCTION_NAME}..."
if aws lambda get-function --function-name "${FUNCTION_NAME}" --region "${REGION}" >/dev/null 2>&1; then
  # Re-running this script is how a rotated API key gets applied, so the
  # environment is updated here too. Updating only the code would leave the
  # previous key in place and the screening endpoint returning 403, which is a
  # confusing way to discover that this branch did nothing.
  echo "  exists, updating image and environment"
  aws lambda update-function-code \
    --function-name "${FUNCTION_NAME}" \
    --image-uri "${IMAGE_URI}" \
    --region "${REGION}" >/dev/null
  aws lambda wait function-updated --function-name "${FUNCTION_NAME}" --region "${REGION}"
  aws lambda update-function-configuration \
    --function-name "${FUNCTION_NAME}" \
    --environment "Variables={TIINGO_API_KEY=${TIINGO_API_KEY},PORT=8080}" \
    --region "${REGION}" >/dev/null

  # wait function-active only tracks State, not LastUpdateStatus, so an
  # asynchronous configuration update that fails (a rotated API key rejected at
  # validation, for example) was reported as success. Poll LastUpdateStatus and
  # fail with the reason instead. The brief settle avoids reading the previous
  # update's "Successful" before Lambda has flipped the status to InProgress.
  sleep 2
  CONFIG_STATUS=""
  for _ in $(seq 1 30); do
    CONFIG_STATUS="$(aws lambda get-function-configuration \
      --function-name "${FUNCTION_NAME}" \
      --region "${REGION}" \
      --query 'LastUpdateStatus' --output text)"
    case "${CONFIG_STATUS}" in
      Successful) break ;;
      Failed)
        echo "Configuration update failed:" >&2
        aws lambda get-function-configuration \
          --function-name "${FUNCTION_NAME}" \
          --region "${REGION}" \
          --query 'LastUpdateStatusReason' --output text >&2
        exit 1
        ;;
      *) sleep 2 ;;
    esac
  done
  if [[ "${CONFIG_STATUS}" != "Successful" ]]; then
    echo "Configuration update did not finish within 60 seconds (last status: ${CONFIG_STATUS})." >&2
    exit 1
  fi
else
  # The execution role's trust policy can take a few seconds to propagate to
  # Lambda even after iam wait role-exists returns. Retry only that specific
  # error, a bounded number of times; any other failure is real and is surfaced
  # at once rather than after ten attempts.
  for attempt in $(seq 1 10); do
    if CREATE_ERR="$(aws lambda create-function \
        --function-name "${FUNCTION_NAME}" \
        --package-type Image \
        --code "ImageUri=${IMAGE_URI}" \
        --role "${ROLE_ARN}" \
        --architectures "${ARCH}" \
        --memory-size "${MEMORY_MB}" \
        --timeout "${TIMEOUT_S}" \
        --environment "Variables={TIINGO_API_KEY=${TIINGO_API_KEY},PORT=8080}" \
        --region "${REGION}" 2>&1)"; then
      break
    fi
    if [[ "${CREATE_ERR}" == *"cannot be assumed by Lambda"* ]]; then
      if [[ "${attempt}" -ge 10 ]]; then
        echo "The execution role was still not assumable by Lambda after 10 attempts." >&2
        echo "Re-run this script once IAM has settled; nothing else needs changing." >&2
        exit 1
      fi
      echo "  role not assumable yet (attempt ${attempt}/10); waiting..."
      sleep 5
      continue
    fi
    echo "${CREATE_ERR}" >&2
    exit 1
  done
fi

aws lambda wait function-active --function-name "${FUNCTION_NAME}" --region "${REGION}"
echo "  ok"

echo "Function URL..."
if aws lambda get-function-url-config --function-name "${FUNCTION_NAME}" --region "${REGION}" >/dev/null 2>&1; then
  echo "  exists"
else
  # auth-type NONE makes the URL public. That is deliberate and matches how the
  # Render deployment is already exposed. Nothing sensitive is behind it.
  aws lambda create-function-url-config \
    --function-name "${FUNCTION_NAME}" \
    --auth-type NONE \
    --region "${REGION}" >/dev/null
fi

# A public Function URL needs TWO permissions, not one. Granting only
# lambda:InvokeFunctionUrl leaves the URL returning 403 Forbidden on every
# request, which looks like a bug in the function rather than in the policy.
#
# The grants are made unconditionally and idempotently. Tying them to URL
# creation meant a run that created the URL but died before adding them could
# never be repaired: every re-run took the "exists" branch, skipped the grants,
# and reported success while the public URL returned 403 forever.
#
# add-permission fails with ResourceConflictException when the statement
# already exists, so check the function's resource policy first and add only
# what is missing.
add_url_permission() {
  local statement_id="$1"
  shift
  local policy
  policy="$(aws lambda get-policy --function-name "${FUNCTION_NAME}" \
    --region "${REGION}" --query Policy --output text 2>/dev/null || true)"
  if grep -qF "${statement_id}" <<<"${policy}"; then
    echo "  permission ${statement_id} already present"
  else
    # Note the flags differ. --function-url-auth-type is rejected on
    # InvokeFunction with "FunctionUrlAuthType is only supported for
    # lambda:InvokeFunctionUrl action"; that action takes
    # --invoked-via-function-url instead.
    aws lambda add-permission \
      --function-name "${FUNCTION_NAME}" \
      --statement-id "${statement_id}" \
      --region "${REGION}" "$@" >/dev/null
  fi
}

add_url_permission FunctionURLAllowPublicAccess \
  --action lambda:InvokeFunctionUrl \
  --principal "*" \
  --function-url-auth-type NONE

add_url_permission FunctionURLAllowInvokeAction \
  --action lambda:InvokeFunction \
  --principal "*" \
  --invoked-via-function-url

URL="$(aws lambda get-function-url-config --function-name "${FUNCTION_NAME}" \
        --region "${REGION}" --query FunctionUrl --output text)"

echo
echo "Deployed."
echo "  URL: ${URL}"
echo
echo "Verify with:"
echo "  curl ${URL}health"
