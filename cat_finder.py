import numpy as np
import boto3
from botocore.exceptions import ClientError
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
    dynamodb_table.put_item(
        Item={
            'Date': date,
            'Timestamp': timestamp,
            'image_name': image_name,
            'label': cat_label,
            'confidence': cat_confidence
        }
    )
    print(f"Added entry to data DynamoDB: {timestamp}, {image_name}, {cat_label}, {cat_confidence}")

def add_to_url_dynamodb(dynamodb_table, s3_url):
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    unique_id = f"{timestamp}_{uuid.uuid4()}"
    dynamodb_table.put_item(
        Item={
            'URL': unique_id,
            'URL_value': s3_url
        }
    )
    print(f"Added entry to URL DynamoDB: {s3_url}")

def upload_to_s3(s3_client, file_name, bucket, object_name=None):
    if object_name is None:
        object_name = os.path.basename(file_name)

    try:
        s3_client.upload_file(file_name, bucket, object_name)
    except ClientError as e:
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

            if is_button_triggered:
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
    load_dotenv()

    missing = [var for var in REQUIRED_ENV_VARS if not os.getenv(var)]
    if missing:
        sys.exit(f"Missing required environment variables: {', '.join(missing)}")

    s3_bucket = os.getenv('S3_BUCKET_NAME')

    DARKNESS_THRESHOLD = 69 # midpoint between lights on and off in the room

    dynamodb = boto3.resource('dynamodb',
        aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
        aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
        region_name=os.getenv('AWS_REGION')
    )
    data_table = dynamodb.Table(os.getenv('DYNAMODB_DATA_TABLE_NAME'))
    url_table = dynamodb.Table(os.getenv('DYNAMODB_URL_TABLE_NAME'))
    
    s3_client = boto3.client('s3',
        aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
        aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
        region_name=os.getenv('AWS_REGION')
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
            
            request.release()
            time.sleep(41)
            
    except KeyboardInterrupt:
        print("Program stopped by user")
    finally:
        picam2.stop()
        pi.stop()

if __name__ == "__main__":
    main()