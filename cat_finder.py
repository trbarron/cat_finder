import numpy as np
import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
import os
from datetime import datetime
import sys
import time
import uuid
import threading
from dotenv import load_dotenv
import pigpio
from picamera2 import Picamera2
from picamera2.devices import IMX500
from picamera2.devices.imx500 import NetworkIntrinsics
from picamera2.devices.imx500.postprocess import softmax

# Transient DNS failures used to propagate out of the main loop and kill the script.
AWS_CONFIG = Config(
    connect_timeout=10,
    read_timeout=10,
    retries={"max_attempts": 3, "mode": "standard"},
)

class TimestampedStream:
    """Prefix each output line with the local time.

    Wrapping the stream leaves the print() calls unchanged. The prefix is only
    added after a newline, so the \\r-redrawing progress bar stays on one line.
    """

    def __init__(self, stream):
        self._stream = stream
        self._at_line_start = True

    def write(self, text):
        lines = text.split("\n")
        for i, line in enumerate(lines):
            if line and self._at_line_start:
                self._stream.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S "))
                self._at_line_start = False
            if line:
                self._stream.write(line)
            if i < len(lines) - 1:
                self._stream.write("\n")
                self._at_line_start = True

    def flush(self):
        self._stream.flush()

    def __getattr__(self, name):
        return getattr(self._stream, name)

class Classification:
    def __init__(self, idx: int, score: float):
        self.idx = idx
        self.score = score

def get_label(labels, idx: int) -> str:
    """Retrieve the label corresponding to the classification index."""
    if idx < 0 or idx >= len(labels):
        print(f"Warning: label index {idx} out of range (0-{len(labels) - 1})")
        return "unknown"
    return labels[idx]

def is_image_too_dark(request, darkness_threshold=30):
    """
    Check if an image is too dark based on average pixel intensity.
    
    Args:
        request: The camera request object containing the image
        darkness_threshold: Threshold below which the image is considered dark (0-255)
        
    Returns:
        bool: True if the image is too dark, False otherwise
    """
    try:
        # Get the image as a numpy array
        arr = request.make_array("main")
        
        # Calculate the average brightness (mean across all pixels and color channels)
        avg_brightness = np.mean(arr)
        
        print(f"Average image brightness: {avg_brightness:.2f}")
        
        # Return True if the image is too dark
        return avg_brightness < darkness_threshold
    except Exception as e:
        print(f"Error checking image brightness: {e}")
        return False

def parse_classification_results(imx500, request, intrinsics, last_detections):
    """Parse the output tensor into classification results."""
    np_outputs = imx500.get_outputs(request.get_metadata())
    if np_outputs is None:
        return last_detections
    
    np_output = np_outputs[0].flatten()
    
    if intrinsics.softmax:
        np_output = softmax(np_output)
    
    array_size = np_output.size
    if array_size == 0:
        print("Warning: Empty output array")
        return last_detections
    
    print(f"Original output shape: {np_outputs[0].shape}")
    print(f"Flattened output size: {array_size}")
    
    num_top = min(3, array_size)
    top_indices = np.argsort(-np_output)[:num_top]
    print(f"Top indices: {top_indices}")
    
    last_detections = [Classification(int(index), float(np_output[index])) for index in top_indices]
    
    print(f"Number of top results: {num_top}")
    print(f"Top scores: {[-np_output[i] for i in top_indices]}")
    
    return last_detections

def add_to_data_dynamodb(dynamodb_table, timestamp, image_name, cat_label, cat_confidence):
    date = timestamp.split('_')[0]
    try:
        dynamodb_table.put_item(
            Item={
                'Date': date,
                'Timestamp': timestamp,
                'image_name': image_name,
                'label': cat_label,
                'confidence': cat_confidence
            }
        )
    except (BotoCoreError, ClientError) as e:
        print(f"Error writing detection to DynamoDB: {e}")
        return False
    print(f"Added entry to data DynamoDB: {timestamp}, {image_name}, {cat_label}, {cat_confidence}")
    return True

def add_to_url_dynamodb(dynamodb_table, s3_url):
    try:
        dynamodb_table.put_item(
            Item={
                'URL': 'url',
                'URL_value': s3_url
            }
        )
    except (BotoCoreError, ClientError) as e:
        print(f"Error writing URL to DynamoDB: {e}")
        return False
    print(f"Added entry to URL DynamoDB: {s3_url}")
    return True

def upload_to_s3(s3_client, file_name, bucket, object_name=None):
    if object_name is None:
        object_name = os.path.basename(file_name)

    try:
        s3_client.upload_file(file_name, bucket, object_name)
    except (BotoCoreError, ClientError) as e:
        # BotoCoreError covers the DNS/connection failures ClientError misses.
        print(f"Error uploading to S3: {e}")
        return None

    s3_url = f"https://{bucket}.s3.amazonaws.com/{object_name}"
    return s3_url

def button_pressed(button_pressed_flag, button_press_lock, gpio, level, tick):
    if level == 0:
        with button_press_lock:
            button_pressed_flag[0] = True
        print("Button press detected")

def process_detection(request, imx500, intrinsics, data_table, url_table, s3_client, labels, s3_bucket=None, is_button_triggered=False, previous_label=None, darkness_threshold=30):
    if is_image_too_dark(request, darkness_threshold):
        print("Image is too dark - classifying as 'none'")
        current_datetime = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        image_name = f"{current_datetime}_{uuid.uuid4()}.jpg"

        # Record the "none" detection if button was pressed
        if is_button_triggered:
            add_to_data_dynamodb(data_table, current_datetime, image_name, "none", 100)

            # Save and upload the dark image if button triggered
            dir_path = os.path.join('imgs', image_name)
            os.makedirs(os.path.dirname(dir_path), exist_ok=True)
            request.save("main", dir_path)

            s3_url = upload_to_s3(s3_client, dir_path, s3_bucket, image_name)
            if s3_url:
                add_to_url_dynamodb(url_table, s3_url)
                if os.path.exists(dir_path):
                    os.remove(dir_path)
                    print(f"Image {dir_path} deleted.")
            else:
                print(f"S3 upload failed, keeping local image: {dir_path}")

        return "none"

    # Proceed with normal classification for images that aren't too dark
    results = parse_classification_results(imx500, request, intrinsics, [])

    current_datetime = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    image_name = f"{current_datetime}_{uuid.uuid4()}.jpg"

    if results:
        top_result = results[0]
        label = get_label(labels, top_result.idx)
        confidence = top_result.score

        print(f'Predicted class: {label} with confidence: {confidence:.2%}')

        if label == previous_label and confidence > 0.75 and label != 'neither':
            print(f"Consecutive detection confirmed: {label}")
            add_to_data_dynamodb(data_table, current_datetime, image_name, label, int(confidence * 100))

        if is_button_triggered and label != 'neither':
            print(f"Button-triggered upload: {label}")
            dir_path = os.path.join('imgs', image_name)
            os.makedirs(os.path.dirname(dir_path), exist_ok=True)
            request.save("main", dir_path)

            s3_url = upload_to_s3(s3_client, dir_path, s3_bucket, image_name)
            if s3_url:
                add_to_url_dynamodb(url_table, s3_url)
                if os.path.exists(dir_path):
                    os.remove(dir_path)
                    print(f"Image {dir_path} deleted.")
            else:
                print(f"S3 upload failed, keeping local image: {dir_path}")

        return label
    return previous_label

REQUIRED_ENV_VARS = [
    'AWS_ACCESS_KEY_ID',
    'AWS_SECRET_ACCESS_KEY',
    'AWS_REGION',
    'DYNAMODB_DATA_TABLE_NAME',
    'DYNAMODB_URL_TABLE_NAME',
    'S3_BUCKET_NAME',
]

def main():
    # Redirected to a file, Python block-buffers stdout: that delayed the log by
    # ~9 minutes and made a healthy run look hung to the watchdog.
    if hasattr(sys.stdout, "reconfigure"):  # a captured StringIO has none
        sys.stdout.reconfigure(line_buffering=True)
        sys.stderr.reconfigure(line_buffering=True)
    sys.stdout = TimestampedStream(sys.stdout)
    sys.stderr = TimestampedStream(sys.stderr)

    load_dotenv()

    missing = [var for var in REQUIRED_ENV_VARS if not os.getenv(var)]
    if missing:
        sys.exit(f"Missing required environment variables: {', '.join(missing)}")

    s3_bucket = os.getenv('S3_BUCKET_NAME')

    DARKNESS_THRESHOLD = 69 # midpoint between lights on and off in the room

    dynamodb = boto3.resource('dynamodb',
        aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
        aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
        region_name=os.getenv('AWS_REGION'),
        config=AWS_CONFIG
    )
    data_table = dynamodb.Table(os.getenv('DYNAMODB_DATA_TABLE_NAME'))
    url_table = dynamodb.Table(os.getenv('DYNAMODB_URL_TABLE_NAME'))
    
    s3_client = boto3.client('s3',
        aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
        aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
        region_name=os.getenv('AWS_REGION'),
        config=AWS_CONFIG
    )
    
    pi = pigpio.pi()
    if not pi.connected:
        exit("Failed to connect to pigpio daemon")
    
    BUTTON_PIN = 17
    pi.set_mode(BUTTON_PIN, pigpio.INPUT)
    pi.set_pull_up_down(BUTTON_PIN, pigpio.PUD_UP)
    
    model_path = "./network.rpk"
    imx500 = IMX500(model_path)
    
    intrinsics = imx500.network_intrinsics
    if not intrinsics:
        intrinsics = NetworkIntrinsics()
        intrinsics.task = "classification"
    elif intrinsics.task != "classification":
        print("Network is not a classification task")
        exit()
    
    labels = intrinsics.labels
    if labels is None:
        with open("./labels.txt", "r") as f:
            labels = f.read().splitlines()
    intrinsics.update_with_defaults()
    
    picam2 = Picamera2(imx500.camera_num)
    config = picam2.create_preview_configuration(
        controls={"FrameRate": intrinsics.inference_rate},
        buffer_count=12
    )
    
    button_pressed_flag = [False]
    button_press_lock = threading.Lock()
    previous_label = None

    pi.callback(BUTTON_PIN, pigpio.FALLING_EDGE, lambda gpio, level, tick: button_pressed(button_pressed_flag, button_press_lock, gpio, level, tick))
    
    imx500.show_network_fw_progress_bar()
    picam2.start(config)
    if intrinsics.preserve_aspect_ratio:
        imx500.set_auto_aspect_ratio()
    
    print("Starting cat detection with darkness filter...")
    try:
        while True:
            request = picam2.capture_request()

            try:
                with button_press_lock:
                    if button_pressed_flag[0]:
                        print("Processing button-triggered image")
                        previous_label = process_detection(request, imx500, intrinsics, data_table, url_table, s3_client, labels,
                                                           s3_bucket=s3_bucket, is_button_triggered=True, previous_label=previous_label, darkness_threshold=DARKNESS_THRESHOLD)
                        button_pressed_flag[0] = False
                    else:
                        print("Processing regular cycle image")
                        previous_label = process_detection(request, imx500, intrinsics, data_table, url_table, s3_client, labels,
                                                           s3_bucket=s3_bucket, previous_label=previous_label, darkness_threshold=DARKNESS_THRESHOLD)
            finally:
                request.release()
            time.sleep(41)
            
    except KeyboardInterrupt:
        print("Program stopped by user")
    finally:
        picam2.stop()
        pi.stop()

if __name__ == "__main__":
    main()