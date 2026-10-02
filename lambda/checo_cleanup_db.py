import boto3
from boto3.dynamodb.conditions import Key
from datetime import datetime, timedelta
import json
from decimal import Decimal

class DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            return str(o)  # Convert Decimal to string
        return super(DecimalEncoder, self).default(o)

def lambda_handler(event, context):
    dynamodb = boto3.resource('dynamodb')
    source_table = dynamodb.Table('catData')
    summary_table = dynamodb.Table('catDataRollup')
    
    yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    
    response = source_table.scan(
        FilterExpression='#date < :yesterday',
        ExpressionAttributeNames={'#date': 'Date'},
        ExpressionAttributeValues={':yesterday': yesterday}
    )
    
    items = response['Items']
    
    grouped_items = {}
    for item in items:
        date = item['Date']
        if date not in grouped_items:
            grouped_items[date] = []
        grouped_items[date].append(item)
    
    for date, day_items in grouped_items.items():
        total_entries = len(day_items)
        
        chunk_item = {
            'Date': date,
            'TotalEntries': total_entries,
            'Entries': day_items
        }
        
        # Convert to DynamoDB-friendly format
        dynamodb_item = json.loads(json.dumps(chunk_item, cls=DecimalEncoder), parse_float=Decimal)
        
        summary_table.put_item(Item=dynamodb_item)
        
        with source_table.batch_writer() as batch:
            for item in items:
                batch.delete_item(
                    Key={
                        'Date': item['Date'],
                        'Timestamp': item['Timestamp']
                    }
                )
    
    return {
        'statusCode': 200,
        'body': f'Successfully aggregated data for {len(grouped_items)} days'
    }