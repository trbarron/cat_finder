# Lambda Functions

AWS Lambda functions that serve cat detection data to the website
(tylerbarron.com cat tracker). Deployment is currently manual (copy/paste
into the Lambda console), so keep this directory in sync when editing
either side.

> **Deployment status:** all four files match the code deployed in us-west-2
> as of 2026-10-02. The 2026-06-10 fixes below are live (both endpoints were
> last updated 2026-06-10).

## Deployment map

| File | Lambda function | Trigger | Reads / writes |
| --- | --- | --- | --- |
| `checo_rest_endpoint.py` | `checoRestEndpoint` | API Gateway `nj3ho46btl`, `GET /checoStage/checoRestEndpoint` | reads `catData` |
| `checo_rest_endpoint_details.py` | `checoRestEndpointDetails` | API Gateway `nj3ho46btl`, `GET /checoStage/checoRestEndpoint/details` | reads `catData`, `catDataRollup` |
| `get_cat_image.py` | `getCatImage` | API Gateway `nj3ho46btl`, `GET /checoStage/checoRestEndpoint/image` | reads `catImageURL`, S3 `catbucketimages` |
| `checo_cleanup_db.py` | `ChecoCleanUpDB` | EventBridge rule `CatDataRollup`, daily 02:00 UTC | archives old `catData` rows to S3, writes per-day summaries to `catDataRollup` |

All run on Python 3.12. The two `checoRestEndpoint*` functions share the IAM
role `checoRestEndpoint-role-eze0wrv5`; `getCatImage` has its own role, and
`ChecoCleanUpDB` runs as `checo-rollup-role` (policy in `iam/`). The Pi
(`cat_finder.py`) writes `catData`, `catImageURL` and `catbucketimages`
directly as the IAM user `checoLogger`.

## Functions

### `checo_rest_endpoint.py`

Today's summary endpoint. Queries `catData` for today's entries (Mountain
Time) and returns:

```json
{
  "work_time": "1:23:45",     // total today, 43 s per detection entry
  "tuni_time": "0:41:52",
  "checo_time": "0:41:53",
  "is_present": true,          // a cat detected in the last 3.75 minutes
  "cat": "Tuni"                // "Tuni" | "Checo" | null
}
```

### `checo_rest_endpoint_details.py`

Historical stats endpoint. Scans `catData` and `catDataRollup` and returns:

```json
{
  "today_work_time": "1:23:45",       // H:MM:SS
  "last_week_work_time": 12.5,        // hours
  "thirty_days_work_time": 50.2,      // hours
  "lifetime_work_time": 400.0,        // hours
  "is_present": true,
  "cat": "Tuni",                       // "Tuni" | "Checo" | null
  "per_cat_work_time": {
    "tuni":  { "today": "0:41:52", "last_week_hours": 6.2,
               "thirty_days_hours": 25.0, "lifetime_hours": 599.7 },
    "checo": { "today": "0:41:53", "last_week_hours": 6.3,
               "thirty_days_hours": 25.2, "lifetime_hours": 1007.3 },
    "other": { "today": "0:00:00", "last_week_hours": 0.0,
               "thirty_days_hours": 0.0, "lifetime_hours": 307.6 }
  },
  "work_time_histogram": [
    // minutes of work per Mountain-Time hour (0-23), last 30 days
    { "hour": 9, "count": 42.3, "tuni": 20.1, "checo": 22.2, "other": 0.0 }
  ]
}
```

`other` holds entries that predate the custom model: ~25,700 historical
entries (~307 h) carry generic classifier labels ("Siamese cat",
"Egyptian cat", "tabby", ...) and can't be attributed to either cat.
Recent data (last week / 30 days) is fully tuni/checo attributed.

`per_cat_work_time` and the histogram's `tuni`/`checo` fields support a
stacked "which cat was working when" visual.

### `get_cat_image.py`

Latest-photo endpoint. Reads the single `catImageURL` item (key
`URL = "url"`), downloads that image from S3 and returns it base64-encoded
with its content type and a human-readable timestamp parsed from the
object name.

### `checo_cleanup_db.py`

Nightly rollup. Scans `catData` (paginated) for entries dated before
yesterday (UTC) and, one day at a time:

1. Merges that day's rows into its S3 archive,
   `s3://catbucketimages/catdata-archive/YYYY/YYYY-MM-DD.json`, keyed by
   `Timestamp`.
2. Rebuilds the day's summary from the whole archive and writes it to
   `catDataRollup`.
3. Deletes only that day's rows from `catData`.

Rows are deleted last, so a failure at any step leaves them in `catData` for
the next run, and the merge means re-runs and late-arriving rows never
double-count.

#### Rollup format (version 2)

One ~1 KB item per day, independent of how busy the day was:

```json
{
  "Date": "2026-09-30",
  "Version": 2,
  "TotalEntries": 702,                       // every row, including "none"
  "CatCounts": {"tuni": 310, "checo": 380, "other": 0},
  "HourlyCatCounts": {"tuni": [0, ..., 0], "checo": [...], "other": [...]},  // 24 Mountain-Time hours
  "ArchiveKey": "catdata-archive/2026/2026-09-30.json"
}
```

`CatCounts` and `HourlyCatCounts` count work entries exactly as the details
endpoint counts raw rows (skipping `none`/`neither`; generic labels go to
`other`), so the details response is unchanged. Before this format, each
item held the day's raw `Entries` (up to 233 KB, close to DynamoDB's 400 KB
item limit); the details endpoint still reads that legacy format, and
`migrate_rollup_v2.py` converts it.

#### Deploying the version-2 rollup

1. Deploy `checo_rest_endpoint_details.py` (reads both formats).
2. Create `checo-rollup-role` from `iam/`, then deploy `checo_cleanup_db.py`
   to `ChecoCleanUpDB` and switch it to that role.
3. From `lambda/`: `python3 migrate_rollup_v2.py --profile <admin>` (dry
   run), then again with `--apply`. Safe to re-run.

Tests: `python3 -m pytest lambda/test_rollup.py`.

## Fixes applied 2026-06-10

- `"none"` and `"neither"` entries no longer count as anyone's work time,
  and no longer satisfy the presence check.
- Timestamps are treated as Mountain Time (what the Pi writes) instead of
  being mis-tagged UTC. The histogram's `(hour + 6) % 24` shift and the
  6 h 3 m presence threshold that compensated for it are removed; the
  details endpoint now uses the same 3.75 min presence threshold as the
  summary endpoint.
- DynamoDB query pagination is followed in the summary endpoint.
- Dead CORS code removed (CORS is handled at the API Gateway layer).

## Breaking changes for the frontend

- Histogram `hour` is now the true Mountain-Time hour (0-23), no +6 offset.
  Any "starts at 5 AM" presentation logic must apply its own offset.
- Histogram `count` is now minutes (43/60 per entry, previously 0.7133) and
  rounded to 2 decimals.
- `is_present` in the details endpoint now means "within 3.75 minutes"
  rather than "within ~6 hours".

## Remaining known quirks

- Work time assumes 43 s per entry while the Pi loop sleeps 41 s per cycle
  (plus processing time).
- The details endpoint scans the full `catData` and `catDataRollup` tables
  on every request. With version-2 rollups that is ~1 KB per day of history
  (about 275 KB for 2024-07 to 2026-09) plus up to two days of raw rows.
- Version 1 of `checo_cleanup_db.py` (replaced 2026-10) deleted every
  scanned row inside its per-day loop, overwrote a day's rollup when late
  rows arrived, didn't paginate its scan, and stored raw entries in one item
  per day. Its logs were also not delivered from Aug 2024 to 2026-10-02.

A frontend-facing summary of these changes lives in the website repo at
`docs/cat-tracker-api-changes.md`.
