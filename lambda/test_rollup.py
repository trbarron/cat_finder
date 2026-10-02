import io
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from botocore.exceptions import ClientError

sys.path.insert(0, os.path.dirname(__file__))

import checo_cleanup_db as cleanup
import checo_rest_endpoint_details as details


def entry(timestamp, label):
    return {'Date': timestamp[:10], 'Timestamp': timestamp, 'label': label,
            'confidence': 99, 'image_name': f'{timestamp}_x.jpg'}


class FakeTable:
    """In-memory DynamoDB table: paginated scan, put/get, batch deletes."""

    def __init__(self, key_names, page_size=2):
        self.key_names = key_names
        self.items = {}
        self.page_size = page_size
        self.fail_put_for = None

    def key(self, item):
        return tuple(item[k] for k in self.key_names)

    def scan(self, FilterExpression=None, ExclusiveStartKey=None):
        rows = sorted(self.items.values(), key=self.key)
        if FilterExpression is not None:
            _, cutoff = FilterExpression._values
            rows = [r for r in rows if r['Date'] < cutoff]
        start = ExclusiveStartKey or 0
        response = {'Items': rows[start:start + self.page_size]}
        if start + self.page_size < len(rows):
            response['LastEvaluatedKey'] = start + self.page_size
        return response

    def put_item(self, Item):
        if Item.get('Date') == self.fail_put_for:
            raise RuntimeError('simulated write failure')
        self.items[self.key(Item)] = Item

    def batch_writer(self):
        table = self

        class Batch:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def delete_item(self, Key):
                table.items.pop(table.key(Key), None)

        return Batch()


class FakeS3:
    def __init__(self):
        self.objects = {}

    def get_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise ClientError({'Error': {'Code': 'NoSuchKey'}}, 'GetObject')
        return {'Body': io.BytesIO(self.objects[(Bucket, Key)])}

    def put_object(self, Bucket, Key, Body, ContentType):
        self.objects[(Bucket, Key)] = Body

    def archived(self, date):
        body = self.objects[(cleanup.ARCHIVE_BUCKET, cleanup.archive_key(date))]
        return json.loads(body)['Entries']


class TestBuildSummary(unittest.TestCase):
    def test_counts_by_cat_and_hour(self):
        summary = cleanup.build_summary('2026-09-30', [
            entry('2026-09-30_10-00-00', 'tuni'),
            entry('2026-09-30_10-30-00', 'tuni'),
            entry('2026-09-30_14-00-00', 'checo'),
            entry('2026-09-30_15-00-00', 'Egyptian cat'),
            entry('2026-09-30_16-00-00', None),
            entry('2026-09-30_17-00-00', 'none'),
            entry('2026-09-30_18-00-00', 'neither'),
        ])
        self.assertEqual(summary['Version'], 2)
        self.assertEqual(summary['TotalEntries'], 7)
        self.assertEqual(summary['CatCounts'], {'tuni': 2, 'checo': 1, 'other': 2})
        self.assertEqual(summary['HourlyCatCounts']['tuni'][10], 2)
        self.assertEqual(summary['HourlyCatCounts']['checo'][14], 1)
        self.assertEqual(summary['HourlyCatCounts']['other'][15], 1)
        self.assertEqual(summary['HourlyCatCounts']['other'][16], 1)
        self.assertEqual(sum(summary['HourlyCatCounts']['other']), 2)
        self.assertEqual(summary['ArchiveKey'], 'catdata-archive/2026/2026-09-30.json')

    def test_skips_unparseable_timestamps(self):
        summary = cleanup.build_summary('2026-09-30', [
            {'Date': '', 'Timestamp': 'garbage', 'label': 'tuni'}])
        self.assertEqual(summary['CatCounts'], {'tuni': 0, 'checo': 0, 'other': 0})

    def test_summary_is_small(self):
        busy_day = [entry(f'2026-09-30_{h:02d}-{m:02d}-00', 'tuni') for h in range(24) for m in range(60)]
        size = len(json.dumps(cleanup.build_summary('2026-09-30', busy_day)))
        self.assertLess(size, 2048)


class TestRollUp(unittest.TestCase):
    def setUp(self):
        self.source = FakeTable(('Date', 'Timestamp'))
        self.summary = FakeTable(('Date',))
        self.s3 = FakeS3()
        self.now = datetime.now(timezone.utc)
        self.old = (self.now - timedelta(days=3)).strftime('%Y-%m-%d')
        self.older = (self.now - timedelta(days=4)).strftime('%Y-%m-%d')
        self.today = self.now.strftime('%Y-%m-%d')
        for row in (entry(f'{self.older}_09-00-00', 'tuni'), entry(f'{self.older}_09-01-00', 'checo'),
                    entry(f'{self.old}_11-00-00', 'checo'), entry(f'{self.old}_11-01-00', 'none'),
                    entry(f'{self.old}_11-02-00', 'tuni'), entry(f'{self.today}_08-00-00', 'tuni')):
            self.source.put_item(row)

    def run_handler(self):
        resources = MagicMock()
        resources.Table.side_effect = lambda name: self.source if name == 'catData' else self.summary
        with patch.object(cleanup.boto3, 'resource', return_value=resources), \
                patch.object(cleanup.boto3, 'client', return_value=self.s3):
            return cleanup.lambda_handler({}, None)

    def test_rolls_up_old_days_and_keeps_recent_rows(self):
        self.run_handler()
        self.assertEqual([k[0] for k in self.source.items], [self.today])
        self.assertEqual(self.summary.items[(self.older,)]['CatCounts'], {'tuni': 1, 'checo': 1, 'other': 0})
        self.assertEqual(self.summary.items[(self.old,)]['CatCounts'], {'tuni': 1, 'checo': 1, 'other': 0})
        self.assertEqual(len(self.s3.archived(self.old)), 3)

    def test_failed_day_keeps_its_rows(self):
        self.summary.fail_put_for = self.old
        with self.assertRaises(RuntimeError):
            self.run_handler()
        # The earlier day completed; the failed day's rows are all still there
        self.assertIn((self.older,), self.summary.items)
        self.assertEqual(sum(1 for k in self.source.items if k[0] == self.old), 3)
        # The next run finishes it without double-counting the archived rows
        self.summary.fail_put_for = None
        self.run_handler()
        self.assertEqual(self.summary.items[(self.old,)]['TotalEntries'], 3)
        self.assertEqual(len(self.s3.archived(self.old)), 3)

    def test_late_rows_merge_into_existing_day(self):
        self.run_handler()
        self.source.put_item(entry(f'{self.old}_12-00-00', 'tuni'))
        self.run_handler()
        rolled = self.summary.items[(self.old,)]
        self.assertEqual(rolled['TotalEntries'], 4)
        self.assertEqual(rolled['CatCounts'], {'tuni': 2, 'checo': 1, 'other': 0})
        self.assertEqual(len(self.s3.archived(self.old)), 4)

    def test_rerun_is_a_no_op(self):
        self.run_handler()
        first = dict(self.summary.items)
        self.run_handler()
        self.assertEqual(self.summary.items, first)


class TestDetailsEndpoint(unittest.TestCase):
    """The details response is identical whether old days are raw or summarised."""

    def response_for(self, rollup_items, raw_items):
        tables = {'catData': FakeTable(('Date', 'Timestamp'), page_size=100),
                  'catDataRollup': FakeTable(('Date',), page_size=100)}
        for row in raw_items:
            tables['catData'].put_item(row)
        for item in rollup_items:
            tables['catDataRollup'].put_item(item)
        resources = MagicMock()
        resources.Table.side_effect = tables.get
        with patch.object(details.boto3, 'resource', return_value=resources):
            return json.loads(details.lambda_handler({}, None)['body'])

    def test_summaries_match_legacy_rollups(self):
        now = datetime.now(ZoneInfo('America/Denver'))
        days = [(now - timedelta(days=n)).strftime('%Y-%m-%d') for n in (2, 5, 20, 45)]
        by_day = {day: [entry(f'{day}_{h:02d}-{m:02d}-00', label)
                        for h, m, label in ((9, 0, 'tuni'), (9, 5, 'checo'), (13, 0, 'Siamese cat'),
                                            (13, 1, 'none'), (22, 59, 'tuni'))]
                  for day in days}
        today_rows = [entry(f"{now.strftime('%Y-%m-%d')}_00-00-01", 'checo')]

        legacy = [{'Date': d, 'TotalEntries': len(e), 'Entries': e} for d, e in by_day.items()]
        summarised = [cleanup.build_summary(d, e) for d, e in by_day.items()]

        self.assertEqual(self.response_for(legacy, today_rows), self.response_for(summarised, today_rows))


if __name__ == '__main__':
    unittest.main()
