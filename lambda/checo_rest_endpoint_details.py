import boto3
from datetime import datetime, timedelta
import json
from zoneinfo import ZoneInfo
from collections import defaultdict
from decimal import Decimal

# Each detection entry represents ~43 seconds of work
SECONDS_PER_ENTRY = 43
MINUTES_PER_ENTRY = SECONDS_PER_ENTRY / 60

# How recently a detection must have occurred for the cat to count as present
PRESENCE_THRESHOLD = timedelta(minutes=3.75)

CATS = ('tuni', 'checo')

# Labels meaning "no cat detected" - not work time for anyone
NO_CAT_LABELS = ('none', 'neither')


def flatten_dynamodb_item(item):
    """Flatten a DynamoDB item by removing type indicators."""
    flattened = {}
    for key, value in item.items():
        if isinstance(value, dict):
            type_key = list(value.keys())[0]
            if type_key == 'N':
                flattened[key] = Decimal(value[type_key])
            elif type_key == 'NULL' and value[type_key]:
                flattened[key] = None
            else:
                flattened[key] = value[type_key]
        else:
            flattened[key] = value
    return flattened


def parse_timestamp(timestamp_str):
    """Parse timestamp string into datetime object, handling different formats."""
    formats = ['%Y-%m-%d_%H-%M-%S', '%Y-%m-%d']
    for fmt in formats:
        try:
            return datetime.strptime(timestamp_str, fmt)
        except ValueError:
            continue
    return None


def scan_table(table):
    """Scan a DynamoDB table and return all items."""
    items = []
    response = table.scan()
    items.extend(response['Items'])
    while 'LastEvaluatedKey' in response:
        response = table.scan(ExclusiveStartKey=response['LastEvaluatedKey'])
        items.extend(response['Items'])
    return items


def unpack_rollup_entries(rollup_items):
    """Unpack entries from catDataRollup items."""
    unpacked_items = []
    for item in rollup_items:
        if 'Entries' in item:
            entries = item['Entries']
            if isinstance(entries, list):
                for entry in entries:
                    if isinstance(entry, dict):
                        if 'M' in entry:
                            unpacked_items.append(flatten_dynamodb_item(entry['M']))
                        else:
                            unpacked_items.append(flatten_dynamodb_item(entry))
                    else:
                        print(f"Unexpected entry type: {type(entry)}")
                        print(f"Entry content: {entry}")
            else:
                print(f"Unexpected 'Entries' type: {type(entries)}")
                print(f"'Entries' content: {entries}")
        else:
            unpacked_items.append(item)
    return unpacked_items


def work_time_hours(entry_count):
    """Convert an entry count to work time in hours."""
    return round(entry_count * SECONDS_PER_ENTRY / 3600, 2)


def work_time_fine_grain(entry_count):
    """Convert an entry count to a H:MM:SS work time string."""
    return str(timedelta(seconds=entry_count * SECONDS_PER_ENTRY))


def lambda_handler(event, context):
    dynamodb = boto3.resource('dynamodb')
    table1 = dynamodb.Table('catData')
    table2 = dynamodb.Table('catDataRollup')

    mountain_tz = ZoneInfo("America/Denver")
    now = datetime.now(mountain_tz)
    today = now.strftime('%Y-%m-%d')
    thirty_days_ago = (now - timedelta(days=30)).strftime('%Y-%m-%d')
    seven_days_ago = (now - timedelta(days=7)).strftime('%Y-%m-%d')

    # Scan both tables and combine
    all_items = scan_table(table1) + unpack_rollup_entries(scan_table(table2))

    # Entry counts per period, broken down by cat. Pre-custom-model rollup
    # data carries generic labels ("Siamese cat", "Egyptian cat", ...) that
    # can't be attributed to either cat; those land in the 'other' bucket.
    periods = ('today', 'week', 'month', 'lifetime')
    counts = {period: defaultdict(int) for period in periods}

    # Minutes of work per Mountain-Time hour over the last 30 days, per cat
    histogram_data = defaultdict(lambda: defaultdict(float))

    most_recent_cat_entry_time = None
    most_recent_cat_label = None

    for item in all_items:
        timestamp_str = item.get('Timestamp') or item.get('Date')
        if not timestamp_str:
            continue  # Skip items without a recognizable timestamp

        item_timestamp = parse_timestamp(timestamp_str)
        if not item_timestamp:
            continue  # Skip items with unparseable timestamps

        label = item.get('label')
        if label in NO_CAT_LABELS:
            continue

        # Timestamps are written by the Pi in Mountain Time
        item_timestamp = item_timestamp.replace(tzinfo=mountain_tz)
        item_date = item_timestamp.strftime('%Y-%m-%d')
        cat_key = label if label in CATS else 'other'

        counts['lifetime'][cat_key] += 1
        if item_date == today:
            counts['today'][cat_key] += 1
        if item_date >= seven_days_ago:
            counts['week'][cat_key] += 1
        if item_date >= thirty_days_ago:
            counts['month'][cat_key] += 1
            histogram_data[item_timestamp.hour][cat_key] += MINUTES_PER_ENTRY

        if label in CATS and (not most_recent_cat_entry_time or item_timestamp > most_recent_cat_entry_time):
            most_recent_cat_entry_time = item_timestamp
            most_recent_cat_label = label

    def period_total(period):
        return sum(counts[period].values())

    is_present = False
    cat = None
    if most_recent_cat_entry_time:
        is_present = (now - most_recent_cat_entry_time) <= PRESENCE_THRESHOLD
        if is_present:
            cat = most_recent_cat_label.capitalize()

    per_cat_work_time = {
        cat_name: {
            "today": work_time_fine_grain(counts['today'][cat_name]),
            "last_week_hours": work_time_hours(counts['week'][cat_name]),
            "thirty_days_hours": work_time_hours(counts['month'][cat_name]),
            "lifetime_hours": work_time_hours(counts['lifetime'][cat_name]),
        }
        for cat_name in (*CATS, 'other')
    }

    # Histogram for Recharts: minutes of work per Mountain-Time hour (0-23)
    # over the last 30 days, with a per-cat split for stacked charts.
    histogram_for_recharts = [
        {
            "hour": hour,
            "count": round(sum(by_cat.values()), 2),
            "tuni": round(by_cat['tuni'], 2),
            "checo": round(by_cat['checo'], 2),
            "other": round(by_cat['other'], 2),
        }
        for hour, by_cat in sorted(histogram_data.items())
    ]

    response_body = {
        "today_work_time": work_time_fine_grain(period_total('today')),
        "last_week_work_time": work_time_hours(period_total('week')),
        "thirty_days_work_time": work_time_hours(period_total('month')),
        "lifetime_work_time": work_time_hours(period_total('lifetime')),
        "is_present": is_present,
        "cat": cat,
        "per_cat_work_time": per_cat_work_time,
        "work_time_histogram": histogram_for_recharts
    }

    json_body = json.dumps(response_body)

    return {
        'statusCode': 200,
        'body': json_body,
        'isBase64Encoded': False
    }
