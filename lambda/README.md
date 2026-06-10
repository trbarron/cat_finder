# Lambda Functions

AWS Lambda functions that serve cat detection data to the website
(tylerbarron.com cat tracker). Deployment is currently manual (copy/paste
into the Lambda console), so keep this directory in sync when editing
either side.

> **Deployment status:** the versions here include bug fixes and new fields
> (2026-06-10) that have NOT been deployed yet. The frontend must be updated
> alongside the details endpoint redeploy — see "Breaking changes" below.

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

## Fixes applied 2026-06-10 (pending redeploy)

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
  (~160k entries as of 2026-06).

A frontend-facing summary of these changes lives in the website repo at
`docs/cat-tracker-api-changes.md`.
