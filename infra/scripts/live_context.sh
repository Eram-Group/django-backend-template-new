#!/usr/bin/env bash
# Print the CDK context that pins the CURRENTLY RUNNING image for one env,
# so a config-only `cdk deploy` never reverts what CD last rolled out.
#
#   usage: live_context.sh <app> <env>   ->  -c image_tag=<tag> -c sentry_release=<sha>
#
# Reads the revision the worker SERVICE runs, not the family's latest ACTIVE
# revision: a failed release leaves a registered-but-never-rolled revision
# behind, and pinning to that would deploy an image nobody validated.
# Exits 1 when the service does not exist yet (first deploy): pass
# `-c image_tag=<sha>` explicitly in that case.
set -euo pipefail

app="${1:?app name}"
env="${2:?env name}"
service="${app}-${env}-worker"  # naming.worker_family = the service name

# shellcheck disable=SC2016 # JMESPath literal, not a shell expansion
if ! arn=$(aws ecs describe-services --cluster "$app" --services "$service" \
      --query 'services[?status==`ACTIVE`] | [0].taskDefinition' --output text 2>/dev/null) \
   || [ -z "$arn" ] || [ "$arn" = "None" ]; then
  echo "no running worker service '$service' - first deploy? pass -c image_tag=<sha>" >&2
  exit 1
fi

json=$(aws ecs describe-task-definition --task-definition "$arn" \
  --query 'taskDefinition.containerDefinitions[0].{image:image,env:environment}' \
  --output json)
image=$(printf '%s' "$json" | jq -r '.image')
tag="${image##*:}"
release=$(printf '%s' "$json" | jq -r '.env[] | select(.name=="SENTRY_RELEASE") | .value')
printf -- '-c image_tag=%s -c sentry_release=%s\n' "$tag" "${release:-$tag}"
