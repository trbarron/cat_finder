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
| `checo_cleanup_db.py` | `ChecoCleanUpDB` | EventBridge rule `CatDataRollup`, daily 02:00 UTC | moves `catData` rows into `catDataRollup` |

All run on Python 3.12. The first, second and fourth share the IAM role
`checoRestEndpoint-role-eze0wrv5`; `getCatImage` has its own role. The Pi
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

Nightly rollup. Scans `catData` for entries dated before yesterday, writes
one `catDataRollup` item per day (`Date`, `TotalEntries`, and the raw
`Entries` list), then deletes the rolled-up rows from `catData`. Keeps
`catData` small so the today view stays a cheap query.

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
  on every request; cost grows with table size. Fine at current volume
  (~160k entries as of 2026-06; `catDataRollup` was 30.6 MB on 2026-10-02).
- `checo_cleanup_db.py` (as deployed 2026-10-02):
  - The delete loop runs inside the per-day loop and deletes every scanned
    row, not just that day's. If a later day's rollup write fails, that
    day's raw rows are already gone.
  - `put_item` replaces an existing rollup for the same date, so rows that
    arrive late for an already-rolled day overwrite it.
  - One rollup item holds a whole day of raw entries, so a very busy day
    could exceed DynamoDB's 400 KB item limit and fail every night.
  - The `catData` scan isn't paginated (first 1 MB only).
  - Its logs were not delivered from Aug 2024 until 2026-10-02, because the
    shared role's logging permission only covered `checoRestEndpoint`.

A frontend-facing summary of these changes lives in the website repo at
`docs/cat-tracker-api-changes.md`.
