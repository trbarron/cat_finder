import boto3
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Attr
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

SOURCE_TABLE = 'catData'
SUMMARY_TABLE = 'catDataRollup'

# Raw entries are archived to S3 as one JSON file per day; the rollup table
# keeps only a small per-day summary (about 1 KB) for the details endpoint.
ARCHIVE_BUCKET = 'catbucketimages'
ARCHIVE_PREFIX = 'catdata-archive'

ROLLUP_VERSION = 2

CATS = ('tuni', 'checo')

# Labels meaning "no cat detected" - not work time for anyone
NO_CAT_LABELS = ('none', 'neither')


def to_plain(value):
    """Convert DynamoDB Decimals (recursively) to int/float for JSON."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: to_plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [to_plain(v) for v in value]
    return value


def parse_timestamp(timestamp_str):
    """Parse timestamp string into datetime object, handling different formats."""
    formats = ['%Y-%m-%d_%H-%M-%S', '%Y-%m-%d']
    for fmt in formats:
        try:
            return datetime.strptime(timestamp_str, fmt)
        except ValueError:
            continue
    return None


def archive_key(date):
    """S3 key for one day's raw entries, e.g. catdata-archive/2026/2026-09-30.json."""
    return f'{ARCHIVE_PREFIX}/{date[:4]}/{date}.json'


def scan_rows_before(table, cutoff_date):
    """Return every catData row dated before cutoff_date, following pagination."""
    rows = []
    kwargs = {'FilterExpression': Attr('Date').lt(cutoff_date)}
    while True:
        response = table.scan(**kwargs)
        rows.extend(response['Items'])
        if 'LastEvaluatedKey' not in response:
            return rows
        kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']


def read_archive(s3, date):
    """Return the archived entries for a day, or [] if none are archived yet."""
    try:
        response = s3.get_object(Bucket=ARCHIVE_BUCKET, Key=archive_key(date))
    except ClientError as e:
        if e.response['Error']['Code'] in ('NoSuchKey', '404'):
            return []
        raise
    return json.loads(response['Body'].read())['Entries']


def merge_into_archive(s3, date, new_entries):
    """Union new entries into the day's archive (by Timestamp) and write it back.

    Re-running for the same rows is a no-op, so a failed or repeated run can
    never double-count. Returns the day's complete entry list.
    """
    merged = {entry['Timestamp']: entry for entry in read_archive(s3, date)}
    for entry in new_entries:
        merged[entry['Timestamp']] = to_plain(entry)
    entries = [merged[ts] for ts in sorted(merged)]
    s3.put_object(
        Bucket=ARCHIVE_BUCKET,
        Key=archive_key(date),
        Body=json.dumps({'Date': date, 'Entries': entries}).encode('utf-8'),
        ContentType='application/json',
    )
    return entries


def build_summary(date, entries):
    """Summarise one day's entries into the version-2 rollup item.

    CatCounts and HourlyCatCounts count work entries per cat ('tuni',
    'checo', or 'other' for pre-custom-model labels), skipping "no cat"
    labels, exactly as the details endpoint counts raw entries.
    """
    cat_counts = defaultdict(int)
    hourly = {cat: [0] * 24 for cat in (*CATS, 'other')}
    for entry in entries:
        timestamp = parse_timestamp(entry.get('Timestamp') or entry.get('Date') or '')
        if not timestamp:
            continue
        label = entry.get('label')
        if label in NO_CAT_LABELS:
            continue
        cat_key = label if label in CATS else 'other'
        cat_counts[cat_key] += 1
        hourly[cat_key][timestamp.hour] += 1
    return {
        'Date': date,
        'Version': ROLLUP_VERSION,
        'TotalEntries': len(entries),
        'CatCounts': {cat: cat_counts[cat] for cat in (*CATS, 'other')},
        'HourlyCatCounts': hourly,
        'ArchiveKey': archive_key(date),
    }


def roll_up_day(s3, source_table, summary_table, date, day_rows):
    """Archive, summarise, then delete one day's rows from catData.

    The rows are deleted only after the archive and summary are written, so a
    failure at any step leaves them in catData for the next run.
    """
    entries = merge_into_archive(s3, date, day_rows)
    summary_table.put_item(Item=build_summary(date, entries))
    with source_table.batch_writer() as batch:
        for row in day_rows:
            batch.delete_item(Key={'Date': row['Date'], 'Timestamp': row['Timestamp']})
    return len(entries)


def lambda_handler(event, context):
    dynamodb = boto3.resource('dynamodb')
    s3 = boto3.client('s3')
    source_table = dynamodb.Table(SOURCE_TABLE)
    summary_table = dynamodb.Table(SUMMARY_TABLE)

    # Keep yesterday and today in catData for the live endpoints
    cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).strftime('%Y-%m-%d')

    rows_by_date = defaultdict(list)
    for row in scan_rows_before(source_table, cutoff):
        rows_by_date[row['Date']].append(row)

    for date in sorted(rows_by_date):
        total = roll_up_day(s3, source_table, summary_table, date, rows_by_date[date])
        print(f"Rolled up {date}: {len(rows_by_date[date])} new rows, {total} archived in total")

    return {
        'statusCode': 200,
        'body': f'Successfully aggregated data for {len(rows_by_date)} days'
    }
