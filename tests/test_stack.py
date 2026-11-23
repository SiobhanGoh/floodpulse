"""Checks the AWS setup without deploying anything: CDK turns the stack
into a CloudFormation template, and these tests read that template."""

import aws_cdk as cdk
from aws_cdk.assertions import Template

from floodpulse.floodpulse_stack import FloodPulseStack


def template() -> Template:
    app = cdk.App()
    stack = FloodPulseStack(app, "Test", env=cdk.Environment(account="123456789012", region="ap-southeast-5"))
    return Template.from_stack(stack)


def test_table_uses_provisioned_mode_within_free_allowance():
    t = template()
    t.has_resource_properties("AWS::DynamoDB::Table", {
        "ProvisionedThroughput": {"ReadCapacityUnits": 5, "WriteCapacityUnits": 10},
        "TimeToLiveSpecification": {"AttributeName": "expires_at", "Enabled": True},
    })


def test_collector_settings():
    template().has_resource_properties("AWS::Lambda::Function", {
        "Handler": "handler.lambda_handler",
        "Runtime": "python3.13",
        "Timeout": 120,
        "MemorySize": 128,
    })


def test_runs_every_15_minutes():
    template().has_resource_properties("AWS::Events::Rule", {
        "ScheduleExpression": "rate(15 minutes)",
        "State": "ENABLED",
    })


def test_bucket_is_private():
    template().has_resource_properties("AWS::S3::Bucket", {
        "PublicAccessBlockConfiguration": {
            "BlockPublicAcls": True,
            "BlockPublicPolicy": True,
            "IgnorePublicAcls": True,
            "RestrictPublicBuckets": True,
        }
    })


def test_collector_can_only_write_to_the_table():
    """Least privilege: no wildcard '*' resources in the collector's policy."""
    policies = template().find_resources("AWS::IAM::Policy")
    collector_policies = [p for name, p in policies.items() if "Collector" in name]
    assert collector_policies
    for policy in collector_policies:
        for statement in policy["Properties"]["PolicyDocument"]["Statement"]:
            actions = statement["Action"] if isinstance(statement["Action"], list) else [statement["Action"]]
            assert statement["Resource"] != "*"
            assert actions == ["dynamodb:PutItem"]
