"""
This script is just a runner / extra UI for the CDK app.
It deploys the CDK app and then prints the links to the logs
but whole logic is in the CDK app - see `app.py`.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import tempfile
from pathlib import Path
from subprocess import run

import boto3

import rich
from rich.progress import Progress
from rich.prompt import Prompt

parser = argparse.ArgumentParser("container-logs")
parser.add_argument("--docker-image", help="A name of a Docker image.", type=str, required=True)
parser.add_argument(
    "--bash-command", help="A bash command (to run inside the Docker image).", type=str, required=True
)
parser.add_argument(
    "--aws-cloudwatch-group", help="A name of an AWS CloudWatch group.", type=str, required=True
)
parser.add_argument(
    "--aws-cloudwatch-stream", help="A name of an AWS CloudWatch stream.", type=str, required=True
)
credentials_group = parser.add_argument_group("AWS credentials")
credentials_group.add_argument(
    "--aws-access-key-id", help="AWS credentials.", type=str, default=None, required=False
)
credentials_group.add_argument(
    "--aws-secret-access-key", help="AWS credentials.", type=str, default=None, required=False
)

parser.add_argument("--aws-region", help="A name of an AWS region", type=str, default=None, required=False)
args = parser.parse_args()

context = [
    "--context",
    f"container_image={args.docker_image}",
    "--context",
    f"container_command_b64={base64.urlsafe_b64encode(args.bash_command.encode()).decode('ascii')}",
    "--context",
    f"log_group_name={args.aws_cloudwatch_group}",
    "--context",
    f"stream_prefix={args.aws_cloudwatch_stream}",
]

env = os.environ.copy()
if args.aws_access_key_id:
    env["AWS_ACCESS_KEY_ID"] = args.aws_access_key_id
if args.aws_secret_access_key:
    env["AWS_SECRET_ACCESS_KEY"] = args.aws_secret_access_key
if args.aws_region:
    context.append("--context")
    context.append(f"region={args.aws_region}")

rich.print(f"[bold magenta]Docker image:[/bold magenta] {args.docker_image}")
rich.print(f"[bold green]Bash command:[/bold green] {args.bash_command}")
rich.print(f"[bold blue]AWS CloudWatch group:[/bold blue] {args.aws_cloudwatch_group}")
rich.print(f"[bold blue]AWS CloudWatch stream:[/bold blue] {args.aws_cloudwatch_stream}")

rich.print("[bold]Deploying the container...[/bold]")
run(["npx", "cdk", "bootstrap", *context], env=env, check=True)

with tempfile.TemporaryDirectory() as tmpdirname:
    outputs_file = Path(tmpdirname) / "outputs.json"
    run(
        [
            "npx",
            "cdk",
            "deploy",
            "--outputs-file",
            outputs_file,
            *context,
        ],
        env=env,
        check=True,
    )
    outputs = json.loads(outputs_file.read_text())
    started_task_arn = outputs["ContainerTask"]["StartedTaskArn"]
    # some naive ARN parsing
    arn = started_task_arn.split(":", 5)
    region = arn[3]
    account = arn[4]
    task = arn[5].split("/", 3)
    ecs_client = boto3.client(
        "ecs",
        region_name=region,
        aws_access_key_id=args.aws_access_key_id,
        aws_secret_access_key=args.aws_secret_access_key,
    )

    rich.print(
        f"[bold]Started task:[/bold] https://{region}.console.aws.amazon.com/ecs/v2/clusters/{task[1]}/tasks/{task[2]}?region={region}"
    )
    with Progress(transient=True) as progress:
        progress.add_task("[italic]Waiting for task to initialize...[/italic]", total=None)
        waiter_run = ecs_client.get_waiter("tasks_running")
        waiter_run.wait(
            cluster=task[1],
            tasks=[task[2]],
            WaiterConfig={"Delay": 5},
        )

    rich.print(
        f"[bold cyan]Logs:[/bold cyan] https://{region}.console.aws.amazon.com/cloudwatch/home?region={region}#logEventViewer:group={args.aws_cloudwatch_group};stream={args.aws_cloudwatch_stream}/Container/{task[2]}",
        end="\n\n",
    )

    rich.print(":question: [bold]What to do?[/bold]")
    action = Prompt.ask(
        "Please enter",
        choices=["Exit", "Stop the task", "Destroy all"],
        default="Exit",
    )
    match action:
        case "Stop the task":
            with Progress(transient=True) as progress:
                progress.add_task("[bold]Stopping the task...[/bold]", total=None)
                ecs_client.stop_task(
                    cluster=task[1],
                    task=task[2],
                    reason="Stopped by container-logs",
                )
                waiter_stop = ecs_client.get_waiter("tasks_stopped")
                waiter_stop.wait(
                    cluster=task[1],
                    tasks=[task[2]],
                    WaiterConfig={"Delay": 5},
                )
            rich.print("[bold] :white_check_mark: Task stopped[/bold]")

        case "Destroy all":
            rich.print("[bold]Destroying all resources...[/bold]")
            run(
                [
                    "npx",
                    "cdk",
                    "destroy",
                    "--force",
                    *context,
                ],
                env=env,
                check=True,
            )

        case _:
            rich.print("[bold]Exiting...[/bold]")
