from __future__ import annotations

import base64
import os
import time

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

task_definition = ecs.FargateTaskDefinition(stack, "TaskDefinition")
task_definition.add_container(
    "Container",
    image=ecs.ContainerImage.from_registry(container_image),
    command=[
        "/bin/sh",
        "-c",
        container_command,
    ],
    health_check=ecs.HealthCheck(
        command=["CMD-SHELL", "exit 0"],
        interval=cdk.Duration.seconds(5),
        start_period=cdk.Duration.seconds(5),
    ),
    logging=ecs.AwsLogDriver(
        log_group=logs.LogGroup.from_log_group_name(
            stack,
            "LogGroup",
            log_group_name=log_group_name,
        ),
        stream_prefix=stream_prefix,
        mode=ecs.AwsLogDriverMode.NON_BLOCKING,
        max_buffer_size=cdk.Size.kibibytes(1),  # default is 1MiB, we want less buffering
    ),
)
# CDK doesn't support `awslogs-create-group` so using an escape hatch
task_definition.node.default_child.add_property_override(
    "ContainerDefinitions.0.LogConfiguration.Options.awslogs-create-group", "true"
)
task_definition.add_to_execution_role_policy(
    iam.PolicyStatement(actions=["logs:CreateLogStream", "logs:CreateLogGroup"], resources=["*"])
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
        "startedBy": time.strftime("cdk-%Y%m%d%H%M%S"),
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
