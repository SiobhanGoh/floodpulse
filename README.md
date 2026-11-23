# FloodPulse

Serverless river-level monitoring for Malaysia, built on AWS.

Every 15 minutes FloodPulse reads the public river-level tables from
[JPS Public Infobanjir](https://publicinfobanjir.water.gov.my/), checks the
readings for errors, and keeps its own history in DynamoDB. Later phases add
Telegram alerts, a "time to danger" estimate, bilingual summaries with Amazon
Bedrock, and a public map.

> **Unofficial learning project.** Always follow official warnings from JPS,
> MetMalaysia and NADMA. Data source: JPS Public Infobanjir.

## Status

| Phase | What | Status |
| --- | --- | --- |
| 0 | Secure AWS account (MFA, budget, IAM admin user, CLI login) | Done |
| 1 | First Lambda built by hand in the console | Done |
| 2 | Infrastructure as code (AWS CDK) | Done |
| 3 | Collector: every 15 minutes, saves clean readings | Live (Selangor) |

## Architecture (so far)

```
EventBridge rule (every 15 min)
        |
        v
Collector Lambda  --->  JPS Public Infobanjir (state tables)
        |
        v
DynamoDB table (one row per station per reading, kept 90 days)
```

Region: Asia Pacific (Malaysia), `ap-southeast-5`.

| Service | Why | Free allowance |
| --- | --- | --- |
| Lambda | Runs the collector only when triggered | Always free: 1M requests, 400,000 GB-seconds a month |
| DynamoDB (provisioned) | Stores readings; TTL deletes rows after 90 days | Always free: 25 GB, 25 RCU, 25 WCU (provisioned only) |
| EventBridge rule | 15-minute timer | |
| S3 | History files and map data (later phases) | |
| CloudWatch Logs | One JSON summary line per run, kept 1 month | |

## Data-quality findings

Found by inspecting live data in Phase 1 (14 Nov 2026):

1. **The Selangor page repeats its table about 8 times** (540 rows for 66
   stations). The collector removes exact copies.
2. **Station IDs are not unique.** ID `3115404` belongs to both
   *Sg. Damansara di Kg. Melayu Subang* (Petaling) and
   *Sg. Jelok di Taman TTDI Grove* (Hulu Langat). Stations are keyed by
   ID + name, e.g. `3115404#sg-jelok-di-taman-ttdi-grove`.
3. **A level of 0 means "no reading".** Sg. Kerling di Kerling read 44.30 m
   on 14 Nov and 0 on 24 Nov; a river cannot drop 44 m. Readings of exactly
   0 are labelled `no_data`.
4. **Some readings are physically implausible** (e.g. 4.21 m at a station
   whose normal level is 50.1 m), most likely a sensor or datum fault.
5. **Tidal stations near the coast can read below 0** (e.g. -0.67 m at
   Sg. Klang di Bandar Klang at low tide). These are real.

Quality labels (only `ok` readings can ever trigger an alert):

| Label | Rule |
| --- | --- |
| `no_data` | level is exactly 0 |
| `bad_thresholds` | thresholds are not in order normal ≤ alert ≤ warning ≤ danger |
| `suspect_high` | level − danger > limit |
| `suspect_low` | normal − level > limit |
| `stale` | reading older than 2 hours |
| `ok` | everything else |

where `limit = max(3 × (danger − normal), 3 m)`. The 3 m floor keeps tidal
stations with a narrow normal-to-danger range from being flagged.

First live run (24 Nov 2026, Selangor, 73 stations): 7 `no_data`,
2 implausibly low, 2 tidal below-zero readings, 2 stale, 60 ok.

## Design decisions

| Decision | Why |
| --- | --- |
| DynamoDB provisioned mode, 5 RCU / 10 WCU | The always-free allowance does not cover on-demand mode |
| Partition key `station_key`, sort key `observed_at` | One station's readings sit together, in time order |
| Conditional write (`attribute_not_exists`) | Re-runs never save the same reading twice |
| Collector may only `dynamodb:PutItem` on its own table | Least privilege; tested in `tests/test_stack.py` |
| No servers, NAT gateways or RDS | Nothing bills while idle |
| `RemovalPolicy.DESTROY` | `cdk destroy` removes everything, so the project can be paused and redeployed |

## Project layout

```
app.py                         CDK entry point (region ap-southeast-5)
floodpulse/floodpulse_stack.py All AWS resources
lambdas/collector/jps.py       Download, read and clean the JPS tables (no AWS code)
lambdas/collector/handler.py   The Lambda: saves new readings to DynamoDB
tests/                         Tests for the parser, the Lambda and the stack
```

## Run the tests

```
python -m venv .venv
.venv\Scripts\activate          # Windows (macOS/Linux: source .venv/bin/activate)
pip install -r requirements-dev.txt
pytest -q
```

## Deploy and remove

Needs Node.js, Python, the AWS CLI (signed in with `aws login`) and the CDK CLI
(`npm install -g aws-cdk`).

```
cdk bootstrap     # once per account and region
cdk deploy        # builds everything
cdk destroy       # removes everything
```

Settings in `cdk.json`: `states` (comma-separated JPS state codes, e.g. `SEL`)
and `schedule_enabled` (`true`/`false`).
