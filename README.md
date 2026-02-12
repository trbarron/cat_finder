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

## Mutation Testing

Mutation testing is used to assess test suite quality by introducing small code changes (mutants) and verifying that tests catch them.

### Step 1: Configuration

Mutation testing is configured in `setup.cfg`:

```ini
[mutmut]
paths_to_mutate=cat_finder.py
tests_dir=.
```

### Step 2: Run Mutation Testing

```bash
mutmut run
```

### Step 3: View Results

```bash
mutmut results > ./mutation_testing/mutmut_results.txt
```

### Step 4: Analyze Survived Mutants

Use the analysis script to gather context for each survived mutant:

```bash
python3 mutation_testing/analyze_survived_mutants.py --output ./mutation_testing/survived_test.json
```

The script will:
1. Parse `mutmut_results.txt` to find survived mutants.
2. For each survived mutant, gather diffs, source context, and relevant tests.
3. Generate a structured JSON file with LLM-ready prompts.

### Step 5: LLM-Assisted Analysis

You can automatically query an LLM to analyze the survived mutants and suggest tests. This requires the `OPENAI_API_KEY` environment variable to be set.

```bash
export OPENAI_API_KEY="your-api-key"
python3 mutation_testing/analyze_survived_mutants.py --output ./mutation_testing/survived_test_report.md --call-llm --limit 5 
```

This command will:
1. Analyze the first 5 survived mutants.
2. Send the context to the configured LLM (default: gpt-4o-mini).
3. Save the LLM's analysis (including whether to write a test and a suggested prompt) into `survived_test.json`.