# Architecture Overview

## Design Philosophy

The architecture follows a two-layer pattern:

1. **Analytical Data Layer** — Flattens DynamoDB data into S3 Parquet format for SQL querying via Athena. This is necessary because DynamoDB cannot efficiently answer analytical queries (aggregations, date ranges, multi-table JOINs).

2. **Intelligence Layer** — A Bedrock Agent that converts natural language questions into SQL, executes them against Athena, and formats the results into readable answers or structured reports.

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│  EXISTING INFRASTRUCTURE (do not modify)                        │
│                                                                 │
│  Platform28 API ──► Lambda Ingestion ──► DynamoDB (calls)       │
│                              │                                  │
│                              └──► Amazon Transcribe ──► S3      │
│                              └──► DynamoDB (scorecards)         │
└─────────────────────────────────────────────────────────────────┘
                         │ DynamoDB Streams
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  NEW: DATA PIPELINE LAYER                                       │
│                                                                 │
│  Stream Processor Lambda                                        │
│    ├── Flattens DynamoDB records (removes type wrappers)        │
│    ├── Enriches with call duration (from Transcribe metadata)   │
│    ├── Writes Parquet to S3 analytics bucket                    │
│    └── Partitioned by: year/month/day                           │
│                                                                 │
│  S3 Analytics Bucket                                            │
│    ├── calls/year=YYYY/month=MM/day=DD/                        │
│    └── scorecards/year=YYYY/month=MM/day=DD/                   │
│                                                                 │
│  AWS Glue Data Catalog                                          │
│    ├── Database: post_call_analytics                            │
│    ├── Table: calls                                             │
│    └── Table: scorecards                                        │
│                                                                 │
│  Amazon Athena (Workgroup: post-call-analytics)                 │
│    └── SQL query engine over S3 Parquet                        │
└─────────────────────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  NEW: INTELLIGENCE LAYER                                        │
│                                                                 │
│  Amazon Bedrock Agent                                           │
│    ├── Model: Claude 3.5 Sonnet                                 │
│    ├── System prompt: schema + business context                 │
│    └── Action Group: AthenaQueryExecutor                        │
│         └── Lambda: executes SQL, returns results               │
│                                                                 │
│  Knowledge Base (optional, Phase 2)                             │
│    └── Transcripts indexed for semantic search                  │
└─────────────────────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  NEW: API & ACCESS LAYER                                        │
│                                                                 │
│  Amazon Cognito User Pool                                       │
│    ├── User management (invite-only)                            │
│    └── JWT token issuance                                       │
│                                                                 │
│  API Gateway (REST)                                             │
│    ├── POST /chat         ← Send question, get answer           │
│    ├── POST /report       ← Generate structured report          │
│    ├── GET  /reports      ← List saved reports                  │
│    └── All routes: Cognito JWT authorizer                       │
│                                                                 │
│  Lambda: API Handler                                            │
│    └── Invokes Bedrock Agent, formats response                  │
└─────────────────────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  NEW: FRONTEND                                                  │
│                                                                 │
│  React SPA (TypeScript)                                         │
│    ├── Hosted on S3 + CloudFront                                │
│    ├── Cognito Hosted UI for login                              │
│    ├── Chat interface for ad-hoc questions                      │
│    └── Report viewer / download                                 │
└─────────────────────────────────────────────────────────────────┘
```

## Data Flow: Ad-Hoc Question

```
1. User types: "How many outbound calls were answered by a human on Feb 11?"
2. React app sends POST /chat with JWT token and question
3. API Gateway validates JWT via Cognito
4. API Handler Lambda invokes Bedrock Agent
5. Bedrock Agent (Claude) generates SQL:
   SELECT COUNT(*) FROM calls
   WHERE direction = 'Outbound'
   AND answer_type = 'Human'
   AND DATE(call_timestamp_utc) = '2026-02-11'
6. Action Group Lambda executes SQL in Athena
7. Athena queries S3 Parquet files
8. Results returned to Agent → formatted into natural language
9. Response streamed back to UI
```

## Data Flow: DynamoDB → S3 Sync

```
1. New/updated record written to DynamoDB calls table
2. DynamoDB Stream fires event to Stream Processor Lambda
3. Lambda flattens the record:
   - Removes DynamoDB type wrappers ({S: "value"} → "value")
   - Extracts nested payload fields to top level
   - Enriches: pulls call duration from Transcribe job metadata
   - Adds partition fields: year, month, day from callTimestampUTC
4. Writes to S3 as line-delimited JSON (Glue/Athena compatible)
   Path: s3://analytics-bucket/calls/year=2026/month=02/day=11/
5. Glue crawler (runs every hour) updates catalog if schema changed
```

## Athena Schema

### calls table

```sql
CREATE EXTERNAL TABLE post_call_analytics.calls (
  call_id             STRING,
  call_timestamp_utc  TIMESTAMP,
  agent_id            STRING,
  agent_name          STRING,
  allocation          STRING,
  direction           STRING,          -- 'Inbound' | 'Outbound'
  file_name           STRING,
  first_or_follow_up  STRING,          -- 'First' | 'Follow-Up'
  medium              STRING,
  program             STRING,
  queue_id            STRING,
  queue_name          STRING,
  session_id          STRING,
  site_id             INT,
  site_name           STRING,
  tenant_id           INT,
  s3_bucket           STRING,
  -- Enriched fields (added by Stream Processor)
  call_duration_seconds  INT,          -- from Transcribe metadata
  answer_type            STRING,       -- 'Human' | 'Voicemail' | 'NoAnswer' | 'Unknown'
  transcript_s3_key      STRING,
  year  STRING,
  month STRING,
  day   STRING
)
PARTITIONED BY (year STRING, month STRING, day STRING)
STORED AS PARQUET
LOCATION 's3://YOUR-ANALYTICS-BUCKET/calls/'
TBLPROPERTIES ('parquet.compress'='SNAPPY');
```

### scorecards table

```sql
CREATE EXTERNAL TABLE post_call_analytics.scorecards (
  guid                  STRING,
  datetime              STRING,
  agent                 STRING,
  call_type             STRING,          -- 'Voicemail' | 'Inbound' | 'Outbound'
  ingested_at           TIMESTAMP,
  notes                 STRING,
  outcome               STRING,          -- 'resolved' | 'unresolved'
  overall_score         DOUBLE,
  primary_intent        STRING,
  resolution_reason     STRING,
  summary               STRING,
  secondary_intent      STRING,
  -- Score sub-fields (flattened from nested map)
  score_ask_for_payment           INT,
  score_confirm_location          INT,
  score_features_advantages       INT,
  score_handle_objections         INT,
  score_size_recommendation       INT,
  score_urgency                   INT,
  -- Evidence sub-fields
  evidence_ask_for_payment        STRING,
  evidence_confirm_location       STRING,
  evidence_features_advantages    STRING,
  evidence_handle_objections      STRING,
  evidence_size_recommendation    STRING,
  evidence_urgency                STRING,
  year  STRING,
  month STRING,
  day   STRING
)
PARTITIONED BY (year STRING, month STRING, day STRING)
STORED AS PARQUET
LOCATION 's3://YOUR-ANALYTICS-BUCKET/scorecards/'
TBLPROPERTIES ('parquet.compress'='SNAPPY');
```

## Key Design Decisions

### Why Athena over OpenSearch?
Athena costs ~$5/TB scanned vs. OpenSearch at $70-200+/month for a cluster. At your data volume, Athena is the right call. Query latency of 2-5 seconds is acceptable for analytics.

### Why Parquet over JSON in S3?
Parquet is columnar — Athena only reads the columns needed for a query, dramatically reducing scan costs and query time. With DynamoDB's nested JSON, Athena performance on raw JSON would be poor.

### Why not just use DynamoDB directly?
DynamoDB cannot do aggregations (COUNT, SUM, GROUP BY), range scans over non-key fields, or JOINs between tables. It would require a full table scan for every analytical query, which is slow and expensive.

### Why Bedrock Agent over a simple prompt?
Agents handle multi-step reasoning, can retry failed queries, and maintain context across a conversation. A user can ask a follow-up like "now break that down by agent" and the agent understands what "that" refers to.

### Answer Type Detection
The `answer_type` field (Human/Voicemail/NoAnswer) is not in the Platform28 payload. Derive it during ingestion:
- If call_duration_seconds < 10 → likely NoAnswer
- If transcript contains only one speaker (agent only) → Voicemail
- If transcript contains both agent and customer → Human
- Check Transcribe speaker labels (`spk_0`, `spk_1`) to determine speaker count
