#!/usr/bin/env python3
"""Entry point for AWS CDK. `cdk deploy` runs this file."""

import os

import aws_cdk as cdk

from floodpulse.floodpulse_stack import FloodPulseStack

app = cdk.App()

FloodPulseStack(
    app,
    "FloodPulse",
    env=cdk.Environment(
        account=os.environ.get("CDK_DEFAULT_ACCOUNT"),  # filled in from `aws login`
        region="ap-southeast-5",                        # Asia Pacific (Malaysia)
    ),
)

app.synth()
