from __future__ import annotations

import base64
import os

import aws_cdk as cdk
import aws_cdk.aws_ec2 as ec2
import aws_cdk.aws_ecs as ecs
import aws_cdk.aws_iam as iam
import aws_cdk.aws_logs as logs
import aws_cdk.custom_resources as cr

app = cdk.App(default_stack_synthesizer=cdk.CliCredentialsStackSynthesizer())

log_group_name = app.node.get_context("log_group_name")
stream_prefix = app.node.get_context("stream_prefix")
container_image = app.node.get_context("container_image")
container_command = (
    base64.urlsafe_b64decode(app.node.get_context("container_command_b64")).decode()
    if app.node.try_get_context("container_command_b64")
    else app.node.get_context("container_command")
)
account = app.node.try_get_context("account")
region = app.node.try_get_context("region")

stack = cdk.Stack(
    app,
    "ContainerTask",
    env=cdk.Environment(
        account=account or os.environ.get("CDK_DEFAULT_ACCOUNT"),
        region=region or os.environ.get("CDK_DEFAULT_REGION"),
    ),
)

vpc = ec2.Vpc.from_lookup(stack, "VPC", is_default=True)
cluster = ecs.Cluster(stack, "LoggingCluster", vpc=vpc, enable_fargate_capacity_providers=True)

log_group_upsert = cr.AwsCustomResource(
    stack,
    "LogGroupUpsert",
    resource_type="Custom::LogGroupUpsert",
    on_create=cr.AwsSdkCall(
        service="CloudwatchLogs",
        action="createLogGroup",
        parameters={"logGroupName": log_group_name},
        ignore_error_codes_matching="ResourceAlreadyExistsException",
        physical_resource_id=cr.PhysicalResourceId.of(log_group_name),
    ),
    policy=cr.AwsCustomResourcePolicy.from_sdk_calls(resources=cr.AwsCustomResourcePolicy.ANY_RESOURCE),
)
log_group = logs.LogGroup.from_log_group_name(
    stack,
    "LogGroup",
    log_group_name=log_group_name,
)
log_group.node.add_dependency(log_group_upsert)

task_definition = ecs.FargateTaskDefinition(stack, "TaskDefinition")
task_definition.add_container(
    "Container",
    image=ecs.ContainerImage.from_registry(container_image),
    command=[
        "/bin/sh",
        "-c",
        container_command,
    ],
    logging=ecs.AwsLogDriver(
        log_group=log_group,
        stream_prefix=stream_prefix,
    ),
)

# running the task
start_task = cr.AwsSdkCall(
    service="ECS",
    action="runTask",
    parameters={
        "capacityProviderStrategy": [
            {"capacityProvider": "FARGATE_SPOT", "weight": 2},
            {"capacityProvider": "FARGATE", "weight": 1},
        ],
        "cluster": cluster.cluster_arn,
        "count": 1,
        "taskDefinition": task_definition.task_definition_arn,
        "networkConfiguration": {
            "awsvpcConfiguration": {
                "subnets": [s.subnet_id for s in vpc.public_subnets],
                "assignPublicIp": "ENABLED",
            }
        },
    },
    physical_resource_id=cr.PhysicalResourceId.from_response("tasks.0.taskArn"),
)
task_runner = cr.AwsCustomResource(
    stack,
    "TaskRunner",
    resource_type="Custom::FargateTaskRunner",
    on_create=start_task,
    on_update=start_task,
    on_delete=cr.AwsSdkCall(
        service="ECS",
        action="stopTask",
        parameters={
            "cluster": cluster.cluster_arn,
            "task": cr.PhysicalResourceIdReference(),
        },
        ignore_error_codes_matching="InvalidParameterException",  # task already stopped
    ),
    policy=cr.AwsCustomResourcePolicy.from_statements(
        [
            iam.PolicyStatement(
                resources=["*"],
                actions=["ECS:RunTask", "ECS:StopTask"],
            ),
            iam.PolicyStatement(
                resources=[
                    task_definition.task_role.role_arn,
                    task_definition.execution_role.role_arn,
                ],
                actions=["iam:PassRole"],
            ),
        ]
    ),
)
task_runner.node.add_dependency(task_definition)

cdk.CfnOutput(stack, "ClusterName", value=cluster.cluster_name)
cdk.CfnOutput(stack, "TaskDefinitionArn", value=task_definition.task_definition_arn)
cdk.CfnOutput(stack, "StartedTaskArn", value=task_runner.get_response_field("tasks.0.taskArn"))

app.synth()
