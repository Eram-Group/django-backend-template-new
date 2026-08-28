"""Web tier: ECS Express Mode service on top of a CDK-owned task definition.

Express Mode provisions and operates the ALB (shared by every Express service
in the VPC), HTTPS listener + certificate, target groups, security groups,
auto scaling and canary deployments with alarm-based rollback. Passing our
own ``task_definition_arn`` is what unlocks ARM64, small task sizes, our log
group and Secrets Manager injection.
"""

from aws_cdk import CfnTag
from aws_cdk import Fn
from aws_cdk import aws_certificatemanager as acm
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_route53 as route53
from aws_cdk import aws_route53_targets as targets
from aws_cdk import custom_resources as cr
from constructs import Construct


class ExpressWebService(Construct):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        service_name: str,
        cluster_name: str,
        infrastructure_role_arn: str,
        task_definition: ecs.TaskDefinition,
        subnet_ids: list[str],
        min_tasks: int,
        max_tasks: int,
        tags: dict[str, str],
    ) -> None:
        super().__init__(scope, construct_id)
        self.service = ecs.CfnExpressGatewayService(
            self,
            "Service",
            service_name=service_name,
            cluster=cluster_name,
            infrastructure_role_arn=infrastructure_role_arn,
            task_definition_arn=task_definition.task_definition_arn,
            health_check_path="/readyz",
            network_configuration=ecs.CfnExpressGatewayService.ExpressGatewayServiceNetworkConfigurationProperty(
                subnets=subnet_ids,
            ),
            scaling_target=ecs.CfnExpressGatewayService.ExpressGatewayScalingTargetProperty(
                min_task_count=min_tasks,
                max_task_count=max_tasks,
                auto_scaling_metric="AVERAGE_CPU",
                auto_scaling_target_value=60,
            ),
            tags=[CfnTag(key=k, value=v) for k, v in tags.items()],
        )

    @property
    def endpoint(self) -> str:
        return self.service.attr_endpoint

    @property
    def service_arn(self) -> str:
        return self.service.attr_service_arn

    def add_custom_domain(
        self,
        *,
        domain_name: str,
        hosted_zone_id: str,
        hosted_zone_name: str,
    ) -> None:
        """Serve ``domain_name`` from the Express-managed ALB.

        Express has no first-class custom domain, so this does what the AWS
        migration guide prescribes by hand: an ACM cert on the HTTPS listener,
        the host added to the Express-owned listener rule, and a Route 53
        alias to the ALB. Everything is read back from the service's managed
        resources, so nothing here depends on the ``Endpoint`` format.
        """
        zone = route53.HostedZone.from_hosted_zone_attributes(
            self, "Zone", hosted_zone_id=hosted_zone_id, zone_name=hosted_zone_name
        )
        certificate = acm.Certificate(
            self,
            "Certificate",
            domain_name=domain_name,
            validation=acm.CertificateValidation.from_dns(zone),
        )
        svc = self.service
        listener_arn = svc.attr_ecs_managed_resource_arns_ingress_path_listener_arn
        rule_arn = svc.attr_ecs_managed_resource_arns_ingress_path_listener_rule_arn
        lb_arn = svc.attr_ecs_managed_resource_arns_ingress_path_load_balancer_arn
        # Managed SGs come back as ARNs (.../security-group/sg-xxx).
        lb_sg_id = Fn.select(
            1,
            Fn.split(
                "/",
                Fn.select(
                    0,
                    svc.attr_ecs_managed_resource_arns_ingress_path_load_balancer_security_groups,
                ),
            ),
        )
        listener = elbv2.ApplicationListener.from_application_listener_attributes(
            self,
            "Listener",
            listener_arn=listener_arn,
            security_group=ec2.SecurityGroup.from_security_group_id(
                self, "LbSg", lb_sg_id
            ),
        )
        elbv2.ApplicationListenerCertificate(
            self,
            "ListenerCertificate",
            listener=listener,
            certificates=[
                elbv2.ListenerCertificate.from_certificate_manager(certificate)
            ],
        )

        sdk_policy = cr.AwsCustomResourcePolicy.from_sdk_calls(
            resources=cr.AwsCustomResourcePolicy.ANY_RESOURCE
        )
        lb_info = cr.AwsCustomResource(
            self,
            "LoadBalancerInfo",
            on_update=cr.AwsSdkCall(
                service="ELBv2",
                action="describeLoadBalancers",
                parameters={"LoadBalancerArns": [lb_arn]},
                physical_resource_id=cr.PhysicalResourceId.of(lb_arn),
            ),
            policy=sdk_policy,
            install_latest_aws_sdk=False,
        )
        rule_info = cr.AwsCustomResource(
            self,
            "RuleInfo",
            on_update=cr.AwsSdkCall(
                service="ELBv2",
                action="describeRules",
                parameters={"RuleArns": [rule_arn]},
                physical_resource_id=cr.PhysicalResourceId.of(rule_arn),
            ),
            policy=sdk_policy,
            install_latest_aws_sdk=False,
        )
        express_host = rule_info.get_response_field(
            "Rules.0.Conditions.0.HostHeaderConfig.Values.0"
        )
        cr.AwsCustomResource(
            self,
            "HostHeaderRule",
            on_update=cr.AwsSdkCall(
                service="ELBv2",
                action="modifyRule",
                parameters={
                    "RuleArn": rule_arn,
                    "Conditions": [
                        {
                            "Field": "host-header",
                            "HostHeaderConfig": {"Values": [express_host, domain_name]},
                        }
                    ],
                },
                physical_resource_id=cr.PhysicalResourceId.of(
                    f"{rule_arn}/{domain_name}"
                ),
            ),
            policy=sdk_policy,
            install_latest_aws_sdk=False,
        )
        alb = elbv2.ApplicationLoadBalancer.from_application_load_balancer_attributes(
            self,
            "Alb",
            load_balancer_arn=lb_arn,
            security_group_id=lb_sg_id,
            load_balancer_dns_name=lb_info.get_response_field(
                "LoadBalancers.0.DNSName"
            ),
            load_balancer_canonical_hosted_zone_id=lb_info.get_response_field(
                "LoadBalancers.0.CanonicalHostedZoneId"
            ),
        )
        route53.ARecord(
            self,
            "AliasRecord",
            zone=zone,
            record_name=domain_name,
            target=route53.RecordTarget.from_alias(targets.LoadBalancerTarget(alb)),
        )
