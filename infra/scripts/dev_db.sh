#!/usr/bin/env bash
# Provision <app>_<env> on the shared dev RDS instance and write DATABASE_URL
# into the <env>/<app> secret - all inside the VPC, nothing printed locally.
#
#   usage: dev_db.sh <app> <env>       (env: dev | staging)
set -euo pipefail
app="${1:?app name}"; env="${2:?env name}"
case "$env" in dev|staging) ;; *) echo "dev/staging only" >&2; exit 2;; esac

output() {
  aws cloudformation describe-stacks --stack-name Shared \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}
family=$(output DevDbBootstrapFamily); sg=$(output DevDbBootstrapSecurityGroupId)
subnets=$(output PublicSubnets); cluster=$(output ClusterName)
overrides=$(jq -cn --arg db "${app}_${env}" --arg secret "${env}/${app}" \
  '{containerOverrides: [{name: "Main", environment: [{name: "APP_DB", value: $db}, {name: "TARGET_SECRET", value: $secret}]}]}')
task=$(aws ecs run-task --cluster "$cluster" --task-definition "$family" --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[${subnets}],securityGroups=[${sg}],assignPublicIp=ENABLED}" \
  --overrides "$overrides" --query 'tasks[0].taskArn' --output text)
echo "started $task" >&2
aws ecs wait tasks-stopped --cluster "$cluster" --tasks "$task"
code=$(aws ecs describe-tasks --cluster "$cluster" --tasks "$task" --query 'tasks[0].containers[0].exitCode' --output text)
echo "exit code: $code (logs: /aws/ecs/${family})" >&2
[ "$code" = "0" ]
