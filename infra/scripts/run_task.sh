#!/usr/bin/env bash
# Run a one-off command on the worker task definition of one environment and
# stream its exit code back (createsu, Sentry smoke, ad-hoc management
# commands). Network settings come from the <app>-App-<env> stack outputs.
#
#   usage: run_task.sh <app> <env> <command...>
set -euo pipefail

app="${1:?app name}"; env="${2:?env name}"; shift 2
[ "$#" -gt 0 ] || { echo "usage: run_task.sh <app> <env> <command...>" >&2; exit 2; }

stack="${app}-App-${env}"
output() {
  aws cloudformation describe-stacks --stack-name "$stack" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}
subnets=$(output Subnets)
sg=$(output SecurityGroup)
family=$(output WorkerFamily)  # naming.worker_family, exported by the stack

overrides=$(jq -cn --args '{containerOverrides: [{name: "Main", command: $ARGS.positional}]}' -- "$@")
task_arn=$(aws ecs run-task --cluster "$app" --task-definition "$family" \
  --launch-type FARGATE --propagate-tags TASK_DEFINITION \
  --network-configuration "awsvpcConfiguration={subnets=[${subnets}],securityGroups=[${sg}],assignPublicIp=ENABLED}" \
  --overrides "$overrides" --query 'tasks[0].taskArn' --output text)
echo "started $task_arn" >&2
aws ecs wait tasks-stopped --cluster "$app" --tasks "$task_arn"
code=$(aws ecs describe-tasks --cluster "$app" --tasks "$task_arn" \
  --query 'tasks[0].containers[0].exitCode' --output text)
echo "exit code: $code (logs: /aws/ecs/${app}-${env}-worker)" >&2
[ "$code" = "0" ]
