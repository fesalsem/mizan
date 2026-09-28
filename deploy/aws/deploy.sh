#!/usr/bin/env bash
#
# Build the Lambda image and push it to ECR.
#
# This is the script to run after changing server.py, index.html or
# requirements.txt. It builds locally, pushes, and (unless SKIP_UPDATE=1)
# points the existing function at the new image.
#
# Safe to re-run. Building the same source twice produces the same layers, so
# the push is mostly a no-op the second time.
#
# Run create-function.sh once first; this script assumes the function exists.
#
# Usage:
#   ./deploy.sh              build, push, update the function
#   SKIP_UPDATE=1 ./deploy.sh   build and push only

set -euo pipefail

REGION="${AWS_REGION:-ap-southeast-1}"
FUNCTION_NAME="${FUNCTION_NAME:-mizan}"
REPO_NAME="${REPO_NAME:-mizan}"
IMAGE_TAG="${IMAGE_TAG:-lambda}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
REGISTRY="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"
IMAGE_URI="${REGISTRY}/${REPO_NAME}:${IMAGE_TAG}"

echo "Building mizan-lambda (${IMAGE_TAG}) from ${REPO_ROOT}..."
# --platform linux/amd64 is explicit. On an Apple silicon or arm64 machine the
# default would be arm64, which the function was not created for, and the
# failure at invoke time is a confusing exec format error.
docker build \
  --platform linux/amd64 \
  --file "${REPO_ROOT}/Dockerfile.lambda" \
  --tag "mizan-lambda:${IMAGE_TAG}" \
  "${REPO_ROOT}"

echo "Logging in to ${REGISTRY}..."
aws ecr get-login-password --region "${REGION}" \
  | docker login --username AWS --password-stdin "${REGISTRY}"

echo "Pushing to ${IMAGE_URI}..."
docker tag "mizan-lambda:${IMAGE_TAG}" "${IMAGE_URI}"
docker push "${IMAGE_URI}"

if [[ "${SKIP_UPDATE:-0}" == "1" ]]; then
  echo "Pushed. SKIP_UPDATE=1, so the function was left on its current image."
  exit 0
fi

echo "Updating function ${FUNCTION_NAME}..."
aws lambda update-function-code \
  --function-name "${FUNCTION_NAME}" \
  --image-uri "${IMAGE_URI}" \
  --region "${REGION}" >/dev/null

aws lambda wait function-updated --function-name "${FUNCTION_NAME}" --region "${REGION}"

URL="$(aws lambda get-function-url-config --function-name "${FUNCTION_NAME}" \
        --region "${REGION}" --query FunctionUrl --output text)"

# The image digest, not the tag. A tag is mutable, so the tag alone does not
# prove which build is live. This is verification criterion 6.
DIGEST="$(aws lambda get-function --function-name "${FUNCTION_NAME}" \
           --region "${REGION}" --query 'Code.ResolvedImageUri' --output text)"

echo
echo "Deployed."
echo "  URL:    ${URL}"
echo "  Image:  ${DIGEST}"
echo
echo "Verify with:"
echo "  curl ${URL}health"
