"""Everything FloodPulse needs in AWS, written as code (AWS CDK).

`cdk deploy` builds all of it. `cdk destroy` removes all of it.
"""

from pathlib import Path

from aws_cdk import (
    Duration,
    RemovalPolicy,
    Stack,
    aws_dynamodb as dynamodb,
    aws_events as events,
    aws_events_targets as targets,
    aws_lambda as _lambda,
    aws_logs as logs,
    aws_s3 as s3,
)
from constructs import Construct

LAMBDA_DIR = Path(__file__).resolve().parent.parent / "lambdas"


class FloodPulseStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        states = self.node.try_get_context("states") or "SEL"
        schedule_on = str(self.node.try_get_context("schedule_enabled")).lower() != "false"

        # ------------------------------------------------------------------
        # 1. DynamoDB table: one row per station per reading time
        # ------------------------------------------------------------------
        # PROVISIONED mode on purpose: the always-free allowance (25 read and
        # 25 write capacity units) does not cover on-demand mode.
        self.readings_table = dynamodb.Table(
            self,
            "ReadingsTable",
            partition_key=dynamodb.Attribute(name="station_key", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="observed_at", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PROVISIONED,
            read_capacity=5,
            write_capacity=10,
            time_to_live_attribute="expires_at",  # rows delete themselves after 90 days
            removal_policy=RemovalPolicy.DESTROY,  # learning project: `cdk destroy` removes it
        )

        # ------------------------------------------------------------------
        # 2. S3 bucket: daily history files and latest.json (used in later phases)
        # ------------------------------------------------------------------
        self.data_bucket = s3.Bucket(
            self,
            "DataBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        # ------------------------------------------------------------------
        # 3. The collector Lambda
        # ------------------------------------------------------------------
        log_group = logs.LogGroup(
            self,
            "CollectorLogs",
            retention=logs.RetentionDays.ONE_MONTH,  # old logs delete themselves
            removal_policy=RemovalPolicy.DESTROY,
        )

        self.collector = _lambda.Function(
            self,
            "Collector",
            runtime=_lambda.Runtime.PYTHON_3_13,
            handler="handler.lambda_handler",
            code=_lambda.Code.from_asset(
                str(LAMBDA_DIR / "collector"), exclude=["__pycache__", "*.pyc"]
            ),
            timeout=Duration.minutes(2),
            memory_size=128,
            log_group=log_group,
            environment={
                "TABLE_NAME": self.readings_table.table_name,
                "STATES": states,
                "KEEP_FOR_DAYS": "90",
            },
            description="Reads JPS river levels and saves new readings",
        )

        # Least privilege: the collector may only ADD rows to this one table.
        # (grant_write_data would also allow deleting, which it never needs.)
        self.readings_table.grant(self.collector, "dynamodb:PutItem")

        # ------------------------------------------------------------------
        # 4. The 15-minute timer
        # ------------------------------------------------------------------
        events.Rule(
            self,
            "Every15Minutes",
            schedule=events.Schedule.rate(Duration.minutes(15)),
            enabled=schedule_on,
            targets=[targets.LambdaFunction(self.collector)],
            description="Starts the FloodPulse collector every 15 minutes",
        )
