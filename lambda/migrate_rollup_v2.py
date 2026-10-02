"""One-time migration of catDataRollup items to the version-2 format.

Legacy rollup items hold a whole day of raw entries (up to ~233 KB, close to
DynamoDB's 400 KB item limit). For each one this archives the raw entries to
S3 and replaces the item with its ~1 KB per-day summary, using the same
functions as the nightly job. Safe to re-run: archives are merged by
Timestamp and summaries rebuilt from the full archive.

Usage:
    python3 migrate_rollup_v2.py --profile default            # dry run
    python3 migrate_rollup_v2.py --profile default --apply    # write
"""
import argparse

import boto3

from checo_cleanup_db import SUMMARY_TABLE, build_summary, merge_into_archive
from checo_rest_endpoint_details import unpack_rollup_entries


def legacy_items(table):
    """Yield rollup items still in the legacy raw-entry format."""
    kwargs = {}
    while True:
        response = table.scan(**kwargs)
        for item in response['Items']:
            if 'CatCounts' not in item:
                yield item
        if 'LastEvaluatedKey' not in response:
            return
        kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--profile', required=True, help='AWS profile with write access')
    parser.add_argument('--region', default='us-west-2')
    parser.add_argument('--apply', action='store_true', help='write changes (default: dry run)')
    args = parser.parse_args()

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    table = session.resource('dynamodb').Table(SUMMARY_TABLE)
    s3 = session.client('s3')

    days = entries_total = 0
    for item in legacy_items(table):
        entries = unpack_rollup_entries([item])
        if args.apply:
            entries = merge_into_archive(s3, item['Date'], entries)
            table.put_item(Item=build_summary(item['Date'], entries))
        days += 1
        entries_total += len(entries)
        print(f"{'migrated' if args.apply else 'would migrate'} {item['Date']}: {len(entries)} entries")

    print(f"{'Migrated' if args.apply else 'Dry run:'} {days} days, {entries_total} entries")


if __name__ == '__main__':
    main()
