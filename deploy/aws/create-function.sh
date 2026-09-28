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
# The image must already be in ECR. Run deploy.sh first if it is not.
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

case "${TIINGO_API_KEY:-}" in
  "")
    echo "TIINGO_API_KEY is not set." >&2
    echo "Either export it, or put a real key in ${ENV_FILE}." >&2
    echo "Usage: TIINGO_API_KEY=xxx $0" >&2
    exit 1
    ;;
  *placeholder*|*replace*|*your_*|*xxx*)
    echo "TIINGO_API_KEY still looks like the placeholder in ${ENV_FILE}." >&2
    echo "Put the real key there, or export a real one." >&2
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
  # Logs only. The function reads no other AWS service, so it needs no other
  # permission. Keeping the policy this narrow is the point of a separate role.
  aws iam attach-role-policy \
    --role-name "${ROLE_NAME}" \
    --policy-arn "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
fi
echo "  ok"

# IAM is eventually consistent. A role created seconds ago is often not yet
# assumable, and create-function fails with "cannot be assumed by Lambda".
echo "Waiting for the role to become assumable..."
for _ in $(seq 1 20); do
  aws iam get-role --role-name "${ROLE_NAME}" --query 'Role.AssumeRolePolicyDocument' >/dev/null 2>&1 \
    && sleep 5 && break
  sleep 3
done

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
else
  aws lambda create-function \
    --function-name "${FUNCTION_NAME}" \
    --package-type Image \
    --code "ImageUri=${IMAGE_URI}" \
    --role "${ROLE_ARN}" \
    --architectures "${ARCH}" \
    --memory-size "${MEMORY_MB}" \
    --timeout "${TIMEOUT_S}" \
    --environment "Variables={TIINGO_API_KEY=${TIINGO_API_KEY},PORT=8080}" \
    --region "${REGION}" >/dev/null
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

  # A public Function URL needs TWO permissions, not one. Granting only
  # lambda:InvokeFunctionUrl leaves the URL returning 403 Forbidden on every
  # request, which looks like a bug in the function rather than in the policy.
  #
  # Note the flags differ. --function-url-auth-type is rejected on
  # InvokeFunction with "FunctionUrlAuthType is only supported for
  # lambda:InvokeFunctionUrl action"; that action takes
  # --invoked-via-function-url instead.
  aws lambda add-permission \
    --function-name "${FUNCTION_NAME}" \
    --statement-id FunctionURLAllowPublicAccess \
    --action lambda:InvokeFunctionUrl \
    --principal "*" \
    --function-url-auth-type NONE \
    --region "${REGION}" >/dev/null

  aws lambda add-permission \
    --function-name "${FUNCTION_NAME}" \
    --statement-id FunctionURLAllowInvokeAction \
    --action lambda:InvokeFunction \
    --principal "*" \
    --invoked-via-function-url \
    --region "${REGION}" >/dev/null
fi

URL="$(aws lambda get-function-url-config --function-name "${FUNCTION_NAME}" \
        --region "${REGION}" --query FunctionUrl --output text)"

echo
echo "Deployed."
echo "  URL: ${URL}"
echo
echo "Verify with:"
echo "  curl ${URL}health"
