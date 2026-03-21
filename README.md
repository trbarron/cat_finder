# Cat Finder

A Raspberry Pi IoT application that detects specific cats using the IMX500 AI accelerator camera module and logs detections to AWS.

## How It Works

The system continuously captures images using a Raspberry Pi camera with the IMX500 AI chip, which runs an on-device classification model. When a cat is detected with high confidence across two consecutive frames, the detection is logged to DynamoDB. A physical button can trigger image capture and upload to S3.

Images that are too dark (below a configurable brightness threshold) are automatically classified as "none" to avoid false positives.

## Hardware Requirements

- Raspberry Pi (tested on Rasp Pi Zero 2 W)
- Raspberry Pi AI Camera (IMX500 sensor)
- Push button connected to GPIO 17
- pigpio daemon running (`sudo pigpiod`)

## Wiring Diagram

```
Raspberry Pi Zero 2 W
┌─────────────────────────────────────┐
│                                     │
│  CSI Camera Port                    │
│  ┌──────────────┐                   │
│  │  ════════════╪═══════════════════════════╗
│  └──────────────┘                   │       ║ Ribbon Cable
│                                     │       ║
│  GPIO Header                        │  ╔════╩══════════════════╗
│  ┌────────┬────────┐                │  ║  IMX500 AI Camera     ║
│  │  2 5V  │ 1 3.3V │                │  ║  (Raspberry Pi        ║
│  │  4 5V  │ 3 GP2  │                │  ║   AI Camera Module)   ║
│  │  6 GND─┼─────────────────────┐  │  ╚═══════════════════════╝
│  │  8 GP14│ 5 GP3  │            │  │
│  │ 10 GP15│ 7 GP4  │            │  │
│  │ 12 GP18│ 9 GND  │            │  │
│  │ 14 GND │11 GP17─┼────────┐   │  │
│  │  ...   │  ...   │        │   │  │
│  └────────┴────────┘        │   │  │
└─────────────────────────────┼───┼──┘
                               │   │
                            ┌──┴───┴──┐
                            │  BUTTON  │
                            │  (Push)  │
                            └──────────┘

Connections:
  GPIO 17 (Pin 11) ──── Button leg A
  GND     (Pin 6)  ──── Button leg B

  The button uses the internal pull-up resistor (configured in software).
  Pressing the button pulls GPIO 17 LOW and triggers an S3 image upload.

  The IMX500 AI Camera connects via the CSI ribbon cable — no additional
  wiring required beyond the camera connector.
```

## Software Dependencies

- Python 3
- numpy
- boto3
- python-dotenv
- pigpio
- picamera2

Install dependencies:

```bash
pip install numpy boto3 python-dotenv
```

`pigpio` and `picamera2` are typically pre-installed on Raspberry Pi OS.

## Setup

1. Clone the repository:

```bash
git clone https://github.com/your-user/cat-finder.git
cd cat-finder/cat_finder
```

2. Copy the example environment file and fill in your AWS credentials:

```bash
cp .env_example .env
```

Required environment variables:
- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
- `AWS_REGION`
- `DYNAMODB_DATA_TABLE_NAME`
- `DYNAMODB_URL_TABLE_NAME`
- `S3_BUCKET_NAME`

3. Place your trained model file as `network.rpk` in the project directory.

4. Ensure `labels.txt` contains your class labels (one per line). Default labels: `neither`, `checo`, `tuni`.

5. Start the pigpio daemon:

```bash
sudo pigpiod
```

## Usage

```bash
python cat_finder.py
```

The script will:
- Validate that all required environment variables are set
- Initialize the camera and AI model
- Continuously classify images every ~41 seconds
- Log confirmed detections (same label twice in a row, >75% confidence) to DynamoDB
- On button press, upload the captured image to S3

Stop with `Ctrl+C`.

### Auto-restart with cron

Use `monitor_script.sh` to automatically restart the script if it stops:

```bash
crontab -e
# Add: */5 * * * * /path/to/monitor_script.sh
```