import boto3
from datetime import datetime, timedelta
from boto3.dynamodb.conditions import Key
import json
from zoneinfo import ZoneInfo
import logging

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Each detection entry represents ~43 seconds of work
SECONDS_PER_ENTRY = 43

# How recently a detection must have occurred for the cat to count as present
PRESENCE_THRESHOLD = timedelta(minutes=3.75)


def query_todays_entries(table, today):
    """Query all of today's entries, following pagination."""
    entries = []
    response = table.query(KeyConditionExpression=Key('Date').eq(today))
    entries.extend(response['Items'])
    while 'LastEvaluatedKey' in response:
        response = table.query(
            KeyConditionExpression=Key('Date').eq(today),
            ExclusiveStartKey=response['LastEvaluatedKey']
        )
        entries.extend(response['Items'])
    return entries


def lambda_handler(event, context):
    dynamodb = boto3.resource('dynamodb')
    table = dynamodb.Table('catData')

    # Set the time zone to Mountain Time (Denver)
    mountain_tz = ZoneInfo("America/Denver")

    # Get current time in Mountain Time
    now = datetime.now(mountain_tz)
    today = now.strftime('%Y-%m-%d')

    logger.info(f"Current time in Mountain Time: {now}")
    logger.info(f"Today's date: {today}")

    entries = query_todays_entries(table, today)
    logger.info(f"Found {len(entries)} entries for today")

    # Count each cat explicitly. The Pi also writes label "none" entries
    # (dark image + button press); those are not work time for either cat.
    tuni_entries = sum(1 for entry in entries if entry.get('label') == 'tuni')
    checo_entries = sum(1 for entry in entries if entry.get('label') == 'checo')
    cat_entries = tuni_entries + checo_entries

    work_time_tuni = str(timedelta(seconds=tuni_entries * SECONDS_PER_ENTRY))
    work_time_checo = str(timedelta(seconds=checo_entries * SECONDS_PER_ENTRY))
    total_work_time = str(timedelta(seconds=cat_entries * SECONDS_PER_ENTRY))

    logger.info(f"Tuni entries: {tuni_entries}, work time: {work_time_tuni}")
    logger.info(f"Checo entries: {checo_entries}, work time: {work_time_checo}")
    logger.info(f"Total work time: {total_work_time}")

    # Check whether a cat was detected recently. Only tuni/checo entries
    # count - a recent "none" entry means nothing was there.
    is_present = False
    cat = None

    cat_detections = [e for e in entries if e.get('label') in ('tuni', 'checo')]
    if cat_detections:
        most_recent_entry = max(cat_detections, key=lambda x: x['Timestamp'])

        # The timestamp in DynamoDB is already in Mountain Time, so attach the timezone
        most_recent_entry_time_str = most_recent_entry['Timestamp']
        most_recent_entry_time = datetime.strptime(most_recent_entry_time_str, '%Y-%m-%d_%H-%M-%S')
        most_recent_entry_time_mt = most_recent_entry_time.replace(tzinfo=mountain_tz)

        logger.info(f"Most recent cat entry timestamp from DB: {most_recent_entry_time_str}")

        current_time_mt = datetime.now(mountain_tz)
        time_diff = current_time_mt - most_recent_entry_time_mt
        logger.info(f"Time difference (both in MT): {time_diff}")

        is_present = time_diff <= PRESENCE_THRESHOLD
        logger.info(f"Is present (with {PRESENCE_THRESHOLD} threshold): {is_present}")

        if is_present:
            cat = "Tuni" if most_recent_entry['label'] == 'tuni' else "Checo"

    # Create the response dictionary
    response_body = {
        "work_time": total_work_time,
        "is_present": is_present,
        "tuni_time": work_time_tuni,
        "checo_time": work_time_checo,
        "cat": cat
    }

    # Convert the response dictionary to a JSON string
    json_body = json.dumps(response_body)

    return {
        'statusCode': 200,
        'body': json_body,
        'isBase64Encoded': False
    }
