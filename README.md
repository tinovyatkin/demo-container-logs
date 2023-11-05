# Docker logs redirection to AWS CloudWatch

## Original problem statement

```md
Write a Python program that accepts the following arguments:

Arguments
1. A name of a Docker image
2. A bash command (to run inside the Docker image)
3. A name of an AWS CloudWatch group
4. A name of an AWS CloudWatch stream
5. AWS credentials
6. A name of an AWS region

Example:
python main.py --docker-image python --bash-command $'pip install pip -U && pip
install tqdm && python -c \"import time\ncounter = 0\nwhile
True:\n\tprint(counter)\n\tcounter = counter + 1\n\ttime.sleep(0.1)\"'
--aws-cloudwatch-group test-task-group-1 --aws-cloudwatch-stream test-task-stream-1
--aws-access-key-id ... --aws-secret-access-key ... --aws-region ...

Functionality
  - The program should create a Docker container using the given Docker image name, and the given bash command
  - The program should handle the output logs of the container and send them to the given AWS CloudWatch group/stream using the given AWS credentials. If the corresponding AWS CloudWatch group or stream does not exist, it should create it using the given AWS credentials.

Other requirements
  - The program should behave properly regardless of how much or what kind of logs the container outputs.
  - The program should gracefully handle errors and interruptions.
```

## System design

The brute-force solution probably would be:

1. Using boto3 and supplied credential to check if CloudWatch log group exists, and [create that](https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/logs/client/create_log_group.html) if it doesn't (or just attempt to create and ignore "already exists" error).
2. Using either [docker SDK for python](https://pypi.org/project/docker/) or simple `subprocess.run` to start specified docker container with host network mode
3. Get docker logs streams and continuously [put events](https://boto3.amazonaws.com/v1/documentation/api/1.9.42/reference/services/events.html#CloudWatchEvents.Client.put_events) into CloudWatch log group

Now, the step 3 is obviously the most complicated one, as it requires proper parsing of _any_ docker logs into CloudWatch structure, particularly things like handling timestamps and multi-line logs.

This is not only boring solution, but also re-inventing the wheel. As we may remember, AWS already provides a way for Docker containters running on ECS to publish their logs into CloudWatch log group.

So, the solution implemented here is demonstrating the use of AWS CDK (Python version) to create specified Fargate Task definition with container logs redirected to CloudWatch log group.

### Bootstrap

Run following script to bootstrap Python venv and install required dependencies:

```sh
./scripts/install-deps.sh
```

### Run

The project actually can be run as a normal CDK deployment providing all required parameters in the CLI command:

```sh
npx cdk deploy --context log_group_name=test-task-group-1 --context stream_prefix=test-task-stream-1 --context container_image=python --context container_command='pip install pip -U && pip install tqdm && python -c \"import time\ncounter = 0\nwhile True:\n\tprint(counter)\n\tcounter = counter + 1\n\ttime.sleep(0.1)\"'
```

For the sake of completeness we also providing [main.py](./main.py) that implements arguments remapping and adds UX as described in the problem statement:

```sh
python main.py --docker-image=python --bash-command $'pip install pip -U && pip install tqdm && python -c \"import time\ncounter = 0\nwhile True:\n\tprint(counter)\n\tcounter = counter + 1\n\ttime.sleep(0.1)\"' --aws-cloudwatch-group test-task-group-1 --aws-cloudwatch-stream test-task-stream-1 --aws-region eu-west-1
```
