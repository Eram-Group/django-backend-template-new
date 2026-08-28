#!/usr/bin/env bash
# Print the CDK context that pins the CURRENTLY DEPLOYED image for one env,
# so a config-only `cdk deploy` never reverts what CD last rolled out.
#
#   usage: live_context.sh <app> <env>   ->  -c image_tag=<tag> -c sentry_release=<sha>
#
# Exits 1 when the worker family has no ACTIVE revision yet (first deploy):
# pass `-c image_tag=<sha>` explicitly in that case.
set -euo pipefail

app="${1:?app name}"
env="${2:?env name}"
family="${app}-${env}-worker"

if ! json=$(aws ecs describe-task-definition --task-definition "$family" \
      --query 'taskDefinition.containerDefinitions[0].{image:image,env:environment}' \
      --output json 2>/dev/null); then
  echo "no ACTIVE task definition for family '$family' - first deploy? pass -c image_tag=<sha>" >&2
  exit 1
fi

image=$(printf '%s' "$json" | jq -r '.image')
tag="${image##*:}"
release=$(printf '%s' "$json" | jq -r '.env[] | select(.name=="SENTRY_RELEASE") | .value')
printf -- '-c image_tag=%s -c sentry_release=%s\n' "$tag" "${release:-$tag}"
