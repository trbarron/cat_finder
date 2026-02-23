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

## Testing

```bash
pip install pytest
python -m pytest test_cat_finder.py -v
```

Tests mock all hardware (camera, GPIO) and AWS dependencies, so they run on any machine.

## Testing

### Unit Tests

Run the test suite:

```bash
pytest test_cat_finder.py -v
```

### Mutation Testing

Mutation testing assesses test quality by introducing code changes (mutants) and checking if tests catch them.

For automated mutation testing and test generation, see the **Agentic Testing Loop** section below.

## Agentic Testing Loop

The **Agentic Testing Loop** is an automated system inspired by Meta's ACH (Automated Compliance Hardening) that uses LLMs to automatically generate tests for survived mutants.

### Overview

Instead of manually writing tests for survived mutants, the agentic loop:
1. Runs mutmut to generate mutations
2. **Triages** survived mutants with LLM (filters out print statements, cosmetic changes, etc.)
3. **Generates test code** automatically with LLM
4. **Applies tests** to test files
5. **Verifies** tests work
6. **Iterates** until coverage improves

### Usage

#### Interactive Mode (Recommended)

Human approves each generated test:

```bash
python -m agentic_testing.cli
```

You'll be prompted to review each test before it's applied:
```
Generated test:
   Class: TestGetLabel
   Method: test_index_equal_to_length
   Explanation: Verifies bounds checking for edge case

Approve this test? [y/n/skip/quit]:
```

#### Auto Mode

Automatically generate tests and create a PR:

```bash
python -m agentic_testing.cli --auto
```

#### Other Options

```bash
# Limit to first 5 mutants (for testing)
python -m agentic_testing.cli --limit 5

# Dry run (preview without modifying files)
python -m agentic_testing.cli --dry-run

# Skip mutmut run (use existing results)
python -m agentic_testing.cli --skip-mutmut
```

### Requirements

```bash
export OPENAI_API_KEY="your-api-key"
```

### How It Works

The system uses a multi-step workflow:

1. **Mutation Generation**: Uses mutmut to create code mutations
2. **Smart Triage**: LLM filters out mutations that don't need tests (print statements, cosmetic changes)
3. **Test Generation**: LLM generates targeted test code for critical mutations
4. **Verification**: Runs pytest to ensure tests pass
5. **Iteration**: Repeats for all triaged mutants

For more details, see [`agentic_testing/README.md`](agentic_testing/README.md).

### Inspiration

This system is based on Meta's ACH approach:
- [Revolutionizing software testing: LLM-powered bug catchers at Meta](https://engineering.fb.com/2025/02/05/security/revolutionizing-software-testing-llm-powered-bug-catchers-meta-ach/)