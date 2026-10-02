import json
import boto3
from botocore.exceptions import ClientError
import urllib.parse
import base64
import re
from datetime import datetime

def parse_timestamp(url):
    # Extract the timestamp part from the URL
    match = re.search(r'/(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})_', url)
    if match:
        timestamp_str = match.group(1)
        # Convert to datetime object
        timestamp = datetime.strptime(timestamp_str, '%Y-%m-%d_%H-%M-%S')
        # Format as a human-readable string
        return timestamp.strftime("%B %d, %Y at %I:%M:%S %p")
    return None

def lambda_handler(event, context):
    # Initialize DynamoDB and S3 clients
    dynamodb = boto3.resource('dynamodb')
    s3 = boto3.client('s3')

    # Get the URL from DynamoDB
    table = dynamodb.Table('catImageURL')
    try:
        response = table.get_item(Key={'URL': 'url'})
    except ClientError as e:
        return {
            'statusCode': 500,
            'body': json.dumps(f"Error retrieving URL from DynamoDB: {e.response['Error']['Message']}")
        }

    # Check if the item was found
    if 'Item' not in response:
        return {
            'statusCode': 404,
            'body': json.dumps("No URL found in the DynamoDB table")
        }

    # Extract the S3 URL
    s3_url = response['Item'].get('URL_value')
    if not s3_url:
        return {
            'statusCode': 404,
            'body': json.dumps("URL field is empty in the DynamoDB table")
        }

    # Parse the timestamp from the URL
    timestamp = parse_timestamp(s3_url)

    # Parse the S3 URL
    parsed_url = urllib.parse.urlparse(s3_url)
    bucket_name = parsed_url.netloc.split('.')[0]
    object_key = parsed_url.path.lstrip('/')

    # Download the image from S3
    try:
        response = s3.get_object(Bucket=bucket_name, Key=object_key)
        image_content = response['Body'].read()
        
        # Encode the image content as base64
        image_base64 = base64.b64encode(image_content).decode('utf-8')
        
        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': response['ContentType']
            },
            'body': json.dumps({
                'image': image_base64,
                'contentType': response['ContentType'],
                'timestamp': timestamp
            }),
            'isBase64Encoded': True
        }
    except ClientError as e:
        return {
            'statusCode': 500,
            'body': json.dumps(f"Error retrieving image from S3: {e.response['Error']['Message']}")
        }