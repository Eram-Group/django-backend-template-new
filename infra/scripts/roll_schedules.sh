#!/usr/bin/env bash
# Point every schedule in one EventBridge Scheduler group at one worker task
# definition revision - the deploy workflow's last step, so scheduled jobs run
# the code that was just rolled out (and a rollback re-dispatch moves them
# back the same way). Idempotent: re-running with the same ARN changes nothing.
#
#   usage: roll_schedules.sh <schedule-group> <task-definition-arn>
set -euo pipefail

group="${1:?schedule group name}"
task_definition_arn="${2:?task definition ARN (with revision)}"

names=$(aws scheduler list-schedules --group-name "$group" \
  --query 'Schedules[].Name' --output text)
[ -n "$names" ] || { echo "no schedules in group '$group'" >&2; exit 1; }

for name in $names; do
  # UpdateSchedule replaces the whole schedule: re-send what GetSchedule
  # returned minus its read-only fields, with only the revision changed.
  aws scheduler get-schedule --group-name "$group" --name "$name" \
    | jq --arg arn "$task_definition_arn" \
        'del(.Arn, .CreationDate, .LastModificationDate)
         | .Target.EcsParameters.TaskDefinitionArn = $arn' \
    > "/tmp/schedule-${name}.json"
  aws scheduler update-schedule --cli-input-json "file:///tmp/schedule-${name}.json" \
    --query 'ScheduleArn' --output text
done
echo "schedules in '$group' now run $task_definition_arn" >&2
