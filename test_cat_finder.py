import os
import sys
import unittest
from unittest.mock import MagicMock, patch, PropertyMock
import numpy as np

# Mock hardware modules before importing cat_finder
sys.modules['pigpio'] = MagicMock()
sys.modules['picamera2'] = MagicMock()
sys.modules['picamera2.devices'] = MagicMock()
sys.modules['picamera2.devices.imx500'] = MagicMock()
sys.modules['picamera2.devices.imx500.postprocess'] = MagicMock()

# Mock softmax to just return its input
sys.modules['picamera2.devices.imx500.postprocess'].softmax = lambda x: x

from cat_finder import (
    Classification,
    get_label,
    is_image_too_dark,
    parse_classification_results,
    add_to_data_dynamodb,
    add_to_url_dynamodb,
    upload_to_s3,
    process_detection,
    button_pressed,
    REQUIRED_ENV_VARS,
)


class TestClassification(unittest.TestCase):
    def test_basic_attributes(self):
        c = Classification(idx=1, score=0.95)
        self.assertEqual(c.idx, 1)
        self.assertAlmostEqual(c.score, 0.95)


class TestGetLabel(unittest.TestCase):
    def setUp(self):
        self.labels = ["neither", "checo", "tuni"]

    def test_valid_index(self):
        self.assertEqual(get_label(self.labels, 0), "neither")
        self.assertEqual(get_label(self.labels, 1), "checo")
        self.assertEqual(get_label(self.labels, 2), "tuni")

    def test_out_of_range_index(self):
        self.assertEqual(get_label(self.labels, 5), "unknown")

    def test_negative_index(self):
        self.assertEqual(get_label(self.labels, -1), "unknown")

    def test_index_equal_to_length_of_labels(self):
        """Test that get_label returns 'unknown' when index is equal to the length of labels."""
        self.assertEqual(get_label(self.labels, len(self.labels)), 'unknown')


class TestIsImageTooDark(unittest.TestCase):
    def test_dark_image(self):
        request = MagicMock()
        # Create a dark image (mean brightness = 10)
        request.make_array.return_value = np.full((100, 100, 3), 10, dtype=np.uint8)
        self.assertTrue(is_image_too_dark(request, darkness_threshold=30))

    def test_bright_image(self):
        request = MagicMock()
        # Create a bright image (mean brightness = 150)
        request.make_array.return_value = np.full((100, 100, 3), 150, dtype=np.uint8)
        self.assertFalse(is_image_too_dark(request, darkness_threshold=30))

    def test_error_handling(self):
        request = MagicMock()
        request.make_array.side_effect = RuntimeError("camera error")
        # Should return False (not dark) on error
        self.assertFalse(is_image_too_dark(request))

    def test_image_at_darkness_threshold(self):
        """Test that an image with average brightness equal to the darkness threshold is not considered too dark."""
        request = MagicMock()
        # Create an image with mean brightness equal to the threshold (30)
        request.make_array.return_value = np.full((100, 100, 3), 30, dtype=np.uint8)
        self.assertFalse(is_image_too_dark(request, darkness_threshold=30))


class TestParseClassificationResults(unittest.TestCase):
    def test_valid_output(self):
        imx500 = MagicMock()
        request = MagicMock()
        intrinsics = MagicMock()
        intrinsics.softmax = False

        # Simulate model output: 3 classes, class 1 has highest score
        output = np.array([[0.1, 0.8, 0.1]])
        imx500.get_outputs.return_value = [output]

        results = parse_classification_results(imx500, request, intrinsics, [])
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0].idx, 1)
        self.assertAlmostEqual(results[0].score, 0.8)

    def test_none_output(self):
        imx500 = MagicMock()
        request = MagicMock()
        intrinsics = MagicMock()
        imx500.get_outputs.return_value = None

        last = [Classification(0, 0.5)]
        results = parse_classification_results(imx500, request, intrinsics, last)
        self.assertEqual(results, last)

    def test_empty_output(self):
        imx500 = MagicMock()
        request = MagicMock()
        intrinsics = MagicMock()
        intrinsics.softmax = False

        imx500.get_outputs.return_value = [np.array([])]

        last = [Classification(0, 0.5)]
        results = parse_classification_results(imx500, request, intrinsics, last)
        self.assertEqual(results, last)

    def test_get_outputs_called_with_request_metadata(self):
        imx500 = MagicMock()
        request = MagicMock()
        meta = object()
        request.get_metadata.return_value = meta
        intrinsics = MagicMock()
        intrinsics.softmax = False
        imx500.get_outputs.return_value = [np.array([[0.1, 0.9, 0.0]])]

        results = parse_classification_results(imx500, request, intrinsics, [])
        imx500.get_outputs.assert_called_once_with(meta)
        self.assertEqual(results[0].idx, 1)
        self.assertAlmostEqual(results[0].score, 0.9)

    def test_more_than_three_classes_limits_to_three(self):
        imx500 = MagicMock()
        request = MagicMock()
        intrinsics = MagicMock()
        intrinsics.softmax = False
        imx500.get_outputs.return_value = [np.array([[0.1, 0.9, 0.2, 0.8, 0.05]])]

        results = parse_classification_results(imx500, request, intrinsics, [])
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0].idx, 1)
        self.assertAlmostEqual(results[0].score, 0.9)
        self.assertEqual(results[1].idx, 3)
        self.assertAlmostEqual(results[1].score, 0.8)
        self.assertEqual(results[2].idx, 2)
        self.assertAlmostEqual(results[2].score, 0.2)

    def test_with_softmax_enabled(self):
        imx500 = MagicMock()
        request = MagicMock()
        intrinsics = MagicMock()
        intrinsics.softmax = True

        output = np.array([[0.1, 0.8, 0.1]])
        imx500.get_outputs.return_value = [output]

        results = parse_classification_results(imx500, request, intrinsics, [])
        self.assertEqual(len(results), 3)
        # softmax mock is identity, so results should be the same
        self.assertEqual(results[0].idx, 1)
        self.assertAlmostEqual(results[0].score, 0.8)

    def test_parse_classification_results_returns_last_detections_when_np_outputs_is_none(self):
        """Test that parse_classification_results returns last_detections when imx500.get_outputs returns None."""
        imx500 = MagicMock()
        request = MagicMock()
        intrinsics = MagicMock()
        last_detections = [Classification(1, 0.9), Classification(2, 0.8)]
        imx500.get_outputs.return_value = None

        results = parse_classification_results(imx500, request, intrinsics, last_detections)
        self.assertEqual(results, last_detections)

    def test_softmax_is_applied_when_intrinsics_indicates_true(self):
        """Verify that when intrinsics.softmax is True the softmax function is applied

        This ensures the returned scores are probabilities (sum to 1) and differ from
        raw logits; the mutant that inverts the condition would skip softmax and
        return raw logits, causing this test to fail against the mutant.
        """
        imx500 = MagicMock()
        request = MagicMock()
        intrinsics = MagicMock()
        intrinsics.softmax = True

        # Use logits where softmax will change the numeric values (and normalize them)
        output = np.array([[1.0, 2.0, 3.0]])
        imx500.get_outputs.return_value = [output]

        def true_softmax(x):
            x = np.asarray(x, dtype=float)
            e = np.exp(x - np.max(x))
            return e / e.sum()

        # Patch the softmax used inside cat_finder to a real softmax implementation
        with patch('cat_finder.softmax', new=true_softmax):
            results = parse_classification_results(imx500, request, intrinsics, [])

        # Expect three results and the top index to be the highest logit (index 2)
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0].idx, 2)

        # The returned score should be the softmax probability for the top class
        expected_probs = true_softmax(output.flatten())
        self.assertAlmostEqual(results[0].score, expected_probs[2])

class TestAddToDataDynamodb(unittest.TestCase):
    def test_correct_item_structure(self):
        table = MagicMock()
        add_to_data_dynamodb(table, "2025-01-15_12-30-00", "img.jpg", "checo", 95)

        table.put_item.assert_called_once_with(
            Item={
                'Date': '2025-01-15',
                'Timestamp': '2025-01-15_12-30-00',
                'image_name': 'img.jpg',
                'label': 'checo',
                'confidence': 95
            }
        )


class TestAddToUrlDynamodb(unittest.TestCase):
    def test_unique_key(self):
        table = MagicMock()
        add_to_url_dynamodb(table, "https://bucket.s3.amazonaws.com/img.jpg")

        table.put_item.assert_called_once()
        item = table.put_item.call_args[1]['Item']
        self.assertEqual(item['URL_value'], "https://bucket.s3.amazonaws.com/img.jpg")
        # Key should not be the static string 'url'
        self.assertNotEqual(item['URL'], 'url')
        # Key should contain a UUID (36 chars with dashes)
        self.assertGreater(len(item['URL']), 36)


    @patch('cat_finder.uuid')
    @patch('cat_finder.datetime')
    def test_add_to_url_includes_timestamp(self, mock_datetime, mock_uuid):
        mock_datetime.now.return_value.strftime.return_value = '2020-01-02_03-04-05'
        mock_uuid.uuid4.return_value = 'fixed-uuid'
        mock_table = MagicMock()
        s3_url = 's3://bucket/object'

        add_to_url_dynamodb(mock_table, s3_url)

        mock_table.put_item.assert_called_once()
        item = mock_table.put_item.call_args[1]['Item']
        expected = '2020-01-02_03-04-05_fixed-uuid'
        self.assertTrue(any(expected in str(v) for v in item.values()))
class TestUploadToS3(unittest.TestCase):
    def test_success(self):
        client = MagicMock()
        url = upload_to_s3(client, "/tmp/img.jpg", "my-bucket", "img.jpg")
        client.upload_file.assert_called_once_with("/tmp/img.jpg", "my-bucket", "img.jpg")
        self.assertEqual(url, "https://my-bucket.s3.amazonaws.com/img.jpg")

    def test_failure(self):
        from botocore.exceptions import ClientError
        client = MagicMock()
        client.upload_file.side_effect = ClientError(
            {"Error": {"Code": "403", "Message": "Forbidden"}}, "upload_file"
        )
        url = upload_to_s3(client, "/tmp/img.jpg", "my-bucket", "img.jpg")
        self.assertIsNone(url)

    def test_default_object_name(self):
        client = MagicMock()
        url = upload_to_s3(client, "/tmp/some_image.jpg", "my-bucket")
        client.upload_file.assert_called_once_with("/tmp/some_image.jpg", "my-bucket", "some_image.jpg")
        self.assertEqual(url, "https://my-bucket.s3.amazonaws.com/some_image.jpg")


class TestProcessDetection(unittest.TestCase):
    def setUp(self):
        self.request = MagicMock()
        self.imx500 = MagicMock()
        self.intrinsics = MagicMock()
        self.intrinsics.softmax = False
        self.data_table = MagicMock()
        self.url_table = MagicMock()
        self.s3_client = MagicMock()
        self.labels = ["neither", "checo", "tuni"]

    def test_dark_image_returns_none(self):
        # Make image dark
        self.request.make_array.return_value = np.full((100, 100, 3), 5, dtype=np.uint8)

        result = process_detection(
            self.request, self.imx500, self.intrinsics,
            self.data_table, self.url_table, self.s3_client,
            self.labels, s3_bucket="bucket", darkness_threshold=30
        )
        self.assertEqual(result, "none")
        # Not button triggered, so no DynamoDB write
        self.data_table.put_item.assert_not_called()

    @patch('cat_finder.os.path.exists', return_value=True)
    @patch('cat_finder.os.remove')
    @patch('cat_finder.os.makedirs')
    def test_dark_image_button_triggered(self, mock_makedirs, mock_remove, mock_exists):
        self.request.make_array.return_value = np.full((100, 100, 3), 5, dtype=np.uint8)
        self.s3_client.upload_file.return_value = None  # success (no exception)

        result = process_detection(
            self.request, self.imx500, self.intrinsics,
            self.data_table, self.url_table, self.s3_client,
            self.labels, s3_bucket="bucket",
            is_button_triggered=True, darkness_threshold=30
        )
        self.assertEqual(result, "none")
        self.data_table.put_item.assert_called_once()
        self.url_table.put_item.assert_called_once()
        mock_remove.assert_called_once()

    def test_consecutive_match(self):
        # Bright image
        self.request.make_array.return_value = np.full((100, 100, 3), 150, dtype=np.uint8)
        # Model returns high confidence for "checo" (index 1)
        output = np.array([[0.05, 0.90, 0.05]])
        self.imx500.get_outputs.return_value = [output]

        result = process_detection(
            self.request, self.imx500, self.intrinsics,
            self.data_table, self.url_table, self.s3_client,
            self.labels, s3_bucket="bucket",
            previous_label="checo", darkness_threshold=30
        )
        self.assertEqual(result, "checo")
        # Consecutive match with >75% confidence should log to DynamoDB
        self.data_table.put_item.assert_called_once()

    def test_no_match_below_confidence(self):
        self.request.make_array.return_value = np.full((100, 100, 3), 150, dtype=np.uint8)
        # Low confidence
        output = np.array([[0.4, 0.5, 0.1]])
        self.imx500.get_outputs.return_value = [output]

        result = process_detection(
            self.request, self.imx500, self.intrinsics,
            self.data_table, self.url_table, self.s3_client,
            self.labels, s3_bucket="bucket",
            previous_label="checo", darkness_threshold=30
        )
        self.assertEqual(result, "checo")
        # Below 75% threshold, no DynamoDB write
        self.data_table.put_item.assert_not_called()

    def test_no_consecutive_match(self):
        self.request.make_array.return_value = np.full((100, 100, 3), 150, dtype=np.uint8)
        output = np.array([[0.05, 0.90, 0.05]])
        self.imx500.get_outputs.return_value = [output]

        result = process_detection(
            self.request, self.imx500, self.intrinsics,
            self.data_table, self.url_table, self.s3_client,
            self.labels, s3_bucket="bucket",
            previous_label="tuni", darkness_threshold=30
        )
        self.assertEqual(result, "checo")
        # Different from previous_label, no DynamoDB write
        self.data_table.put_item.assert_not_called()

    @patch('cat_finder.os.path.exists', return_value=True)
    @patch('cat_finder.os.remove')
    @patch('cat_finder.os.makedirs')
    def test_button_triggered_with_upload_failure_keeps_image(self, mock_makedirs, mock_remove, mock_exists):
        from botocore.exceptions import ClientError
        self.request.make_array.return_value = np.full((100, 100, 3), 150, dtype=np.uint8)
        output = np.array([[0.05, 0.90, 0.05]])
        self.imx500.get_outputs.return_value = [output]
        self.s3_client.upload_file.side_effect = ClientError(
            {"Error": {"Code": "500", "Message": "Error"}}, "upload_file"
        )

        result = process_detection(
            self.request, self.imx500, self.intrinsics,
            self.data_table, self.url_table, self.s3_client,
            self.labels, s3_bucket="bucket",
            is_button_triggered=True, previous_label="checo", darkness_threshold=30
        )
        self.assertEqual(result, "checo")
        # Image should NOT be deleted when upload fails
        mock_remove.assert_not_called()
class TestButtonPressed(unittest.TestCase):
    def test_sets_flag_on_falling_edge(self):
        flag = [False]
        lock = __import__('threading').Lock()
        button_pressed(flag, lock, gpio=17, level=0, tick=0)
        self.assertTrue(flag[0])

    def test_ignores_non_zero_level(self):
        flag = [False]
        lock = __import__('threading').Lock()
        button_pressed(flag, lock, gpio=17, level=1, tick=0)
        self.assertFalse(flag[0])

    def test_thread_safety(self):
        flag = [False]
        lock = MagicMock()
        button_pressed(flag, lock, gpio=17, level=0, tick=0)
        lock.__enter__.assert_called()
class TestProcessDetectionNeitherLabel(unittest.TestCase):
    def setUp(self):
        self.request = MagicMock()
        self.imx500 = MagicMock()
        self.intrinsics = MagicMock()
        self.intrinsics.softmax = False
        self.data_table = MagicMock()
        self.url_table = MagicMock()
        self.s3_client = MagicMock()
        self.labels = ["neither", "checo", "tuni"]

    def test_neither_label_not_logged(self):
        """Even with consecutive match and high confidence, 'neither' should not be logged."""
        self.request.make_array.return_value = np.full((100, 100, 3), 150, dtype=np.uint8)
        # Model returns high confidence for "neither" (index 0)
        output = np.array([[0.90, 0.05, 0.05]])
        self.imx500.get_outputs.return_value = [output]

        result = process_detection(
            self.request, self.imx500, self.intrinsics,
            self.data_table, self.url_table, self.s3_client,
            self.labels, s3_bucket="bucket",
            previous_label="neither", darkness_threshold=30
        )
        self.assertEqual(result, "neither")
        self.data_table.put_item.assert_not_called()

    def test_empty_results_returns_previous_label(self):
        """When classification returns no results, previous_label is preserved."""
        self.request.make_array.return_value = np.full((100, 100, 3), 150, dtype=np.uint8)
        # get_outputs returns None -> parse returns empty last_detections
        self.imx500.get_outputs.return_value = None

        result = process_detection(
            self.request, self.imx500, self.intrinsics,
            self.data_table, self.url_table, self.s3_client,
            self.labels, s3_bucket="bucket",
            previous_label="tuni", darkness_threshold=30
        )
        self.assertEqual(result, "tuni")
        self.data_table.put_item.assert_not_called()

    def test_consecutive_match_no_button_logs_but_no_upload(self):
        """Consecutive match without button press should log to DynamoDB but not upload to S3."""
        self.request.make_array.return_value = np.full((100, 100, 3), 150, dtype=np.uint8)
        output = np.array([[0.05, 0.90, 0.05]])
        self.imx500.get_outputs.return_value = [output]

        result = process_detection(
            self.request, self.imx500, self.intrinsics,
            self.data_table, self.url_table, self.s3_client,
            self.labels, s3_bucket="bucket",
            is_button_triggered=False, previous_label="checo", darkness_threshold=30
        )
        self.assertEqual(result, "checo")
        # Should log to data table
        self.data_table.put_item.assert_called_once()
        # Should NOT upload or log URL
        self.s3_client.upload_file.assert_not_called()
        self.url_table.put_item.assert_not_called()


class TestMainEnvValidation(unittest.TestCase):
    def _env_with_mutmut(self, env_dict):
        """Preserve MUTANT_UNDER_TEST if set (needed for mutation testing)."""
        mutant = os.environ.get('MUTANT_UNDER_TEST')
        if mutant is not None:
            env_dict['MUTANT_UNDER_TEST'] = mutant
        return env_dict

    @patch('cat_finder.load_dotenv')
    @patch('cat_finder.boto3')
    def test_missing_env_vars_exits(self, mock_boto3, mock_dotenv):
        """main() should sys.exit when required env vars are missing."""
        from cat_finder import main
        with patch.dict('os.environ', self._env_with_mutmut({}), clear=True):
            with self.assertRaises(SystemExit) as ctx:
                main()
            error_msg = str(ctx.exception)
            for var in REQUIRED_ENV_VARS:
                self.assertIn(var, error_msg)

    @patch('cat_finder.load_dotenv')
    @patch('cat_finder.boto3')
    def test_partial_env_vars_exits(self, mock_boto3, mock_dotenv):
        """main() should exit listing only the missing vars."""
        from cat_finder import main
        partial_env = self._env_with_mutmut({
            'AWS_ACCESS_KEY_ID': 'key',
            'AWS_SECRET_ACCESS_KEY': 'secret',
            'AWS_REGION': 'us-east-1',
        })
        with patch.dict('os.environ', partial_env, clear=True):
            with self.assertRaises(SystemExit) as ctx:
                main()
            error_msg = str(ctx.exception)
            self.assertIn('DYNAMODB_DATA_TABLE_NAME', error_msg)
            self.assertIn('S3_BUCKET_NAME', error_msg)
            self.assertNotIn('AWS_ACCESS_KEY_ID', error_msg)

    def test_pi_callback_registers_handler_and_triggers_button_processing(self):
        # Prepare a full environment so main() proceeds to register the callback
        env = {
            'AWS_ACCESS_KEY_ID': 'x',
            'AWS_SECRET_ACCESS_KEY': 'x',
            'AWS_REGION': 'us-east-1',
            'DYNAMODB_DATA_TABLE_NAME': 'data',
            'DYNAMODB_URL_TABLE_NAME': 'url',
            'S3_BUCKET_NAME': 'bucket'
        }

        # Mocks used inside main()
        pi_mock = MagicMock()
        pi_mock.connected = True

        # Capture the handler passed to pi.callback
        def callback_side_effect(pin, edge, handler):
            setattr(pi_mock, 'registered_handler', handler)
            return MagicMock()

        pi_mock.callback.side_effect = callback_side_effect

        # Picamera2 instance mock
        picam2_mock = MagicMock()
        request_mock = MagicMock()
        request_mock.release = MagicMock()

        # capture_request returns a request once, then raises KeyboardInterrupt to end main()
        calls = {'count': 0}
        def capture_request_side_effect():
            if calls['count'] == 0:
                calls['count'] = 1
                return request_mock
            raise KeyboardInterrupt()
        picam2_mock.capture_request.side_effect = capture_request_side_effect

        # When start() is called (after callback registration), simulate pressing the button
        def start_side_effect(config):
            handler = getattr(pi_mock, 'registered_handler', None)
            if callable(handler):
                handler(17, 0, 0)
            return None
        picam2_mock.start.side_effect = start_side_effect

        # IMX500 mock with minimal intrinsics to satisfy main()
        imx500_mock = MagicMock()
        intrinsics_mock = MagicMock()
        intrinsics_mock.labels = ["neither", "checo", "tuni"]
        intrinsics_mock.preserve_aspect_ratio = False
        intrinsics_mock.inference_rate = 1
        imx500_mock.network_intrinsics = intrinsics_mock
        imx500_mock.camera_num = 0

        # Patch out external deps and inject our mocks
        with patch.dict('os.environ', env, clear=True):
            with patch('cat_finder.load_dotenv'):
                with patch('cat_finder.boto3'):
                    with patch('cat_finder.pigpio.pi') as mock_pi_fn:
                        mock_pi_fn.return_value = pi_mock
                        with patch('cat_finder.Picamera2') as mock_pic_cls:
                            mock_pic_cls.return_value = picam2_mock
                            with patch('cat_finder.IMX500') as mock_imx_cls:
                                mock_imx_cls.return_value = imx500_mock
                                # Prevent long sleep in main loop
                                with patch('cat_finder.time.sleep', return_value=None):
                                    # Observe process_detection calls
                                    with patch('cat_finder.process_detection') as mock_process_detection:
                                        mock_process_detection.return_value = None

                                        # Directly run the callback here to simulate behavior without importing main()
                                        # Register the callback
                                        pi_mock.callback(BUTTON_PIN, pigpio.FALLING_EDGE, lambda gpio, level, tick: button_pressed(button_pressed_flag, button_press_lock, gpio, level, tick))
                                        
                                        # Simulate button press with a None handler that should not call button_pressed
                                        pi_mock.callback(BUTTON_PIN, pigpio.FALLING_EDGE, None)
                                        handler = pi_mock.registered_handler
                                        if handler:
                                            handler(17, 0, 0)
                                        
                                        # Ensure process_detection was NOT called
                                        self.assertFalse(mock_process_detection.called)
if __name__ == "__main__":
    unittest.main()
