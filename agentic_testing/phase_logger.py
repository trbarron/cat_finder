#!/usr/bin/env python3
"""
Per-phase logging for the agentic testing pipeline.

Each phase writes to its own log file inside a timestamped run directory:
  .agentic_testing_cache/logs/run_YYYYMMDD_HHMMSS/
    1_mutation.log      - Mutation generation output
    2_parsing.log       - Mutant parsing and testing
    3_triage.log        - LLM triage decisions (prompt + response)
    4_prefilter.log     - Bulk pre-filter results
    5_generation.log    - LLM test generation (prompt + response)
    6_fixing.log        - LLM test fixing iterations
    7_verification.log  - Verification results (original + mutant)
    summary.log         - Final summary and stats
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


class PhaseLogger:
    """Manages per-phase log files for a single pipeline run."""

    PHASES = {
        "mutation": "1_mutation.log",
        "parsing": "2_parsing.log",
        "triage": "3_triage.log",
        "prefilter": "4_prefilter.log",
        "generation": "5_generation.log",
        "fixing": "6_fixing.log",
        "verification": "7_verification.log",
        "summary": "summary.log",
    }

    def __init__(self, cache_dir: Path):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.run_dir = cache_dir / "logs" / f"run_{timestamp}"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._handles: dict[str, Path] = {}

        # Initialize all log files with headers
        for phase, filename in self.PHASES.items():
            path = self.run_dir / filename
            path.write_text(
                f"{'='*80}\n"
                f"Phase: {phase}\n"
                f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"{'='*80}\n\n"
            )
            self._handles[phase] = path

    def log(self, phase: str, message: str):
        """Append a message to the given phase log."""
        path = self._handles.get(phase)
        if not path:
            return
        with open(path, "a") as f:
            f.write(message + "\n")

    def log_divider(self, phase: str, label: str = ""):
        """Write a visual divider, optionally with a label."""
        line = f"\n{'─'*60}"
        if label:
            line += f"\n{label}"
            line += f"\n{'─'*60}"
        self.log(phase, line)

    # ── Convenience helpers per phase ──────────────────────────

    def log_mutation(self, engine: str, source_file: str, output: str):
        self.log("mutation", f"Engine: {engine}")
        self.log("mutation", f"Source: {source_file}")
        self.log_divider("mutation", "Output")
        self.log("mutation", output)

    def log_parsing(self, message: str):
        self.log("parsing", message)

    def log_triage_request(self, mutant_id: str, prompt: str):
        self.log_divider("triage", f"Mutant: {mutant_id}")
        self.log("triage", "PROMPT:")
        self.log("triage", prompt)

    def log_triage_response(self, mutant_id: str, response: dict):
        self.log("triage", f"\nRESPONSE for {mutant_id}:")
        self.log("triage", json.dumps(response, indent=2))

    def log_prefilter(self, mutant_id: str, killed: bool, detail: str = ""):
        status = "KILLED" if killed else "SURVIVED"
        self.log("prefilter", f"  {mutant_id}: {status}  {detail}")

    def log_prefilter_summary(self, killed: int, remaining: int):
        self.log("prefilter", f"\nPre-filter complete: {killed} already killed, {remaining} remaining")

    def log_generation_request(self, mutant_id: str, agent_prompt: str):
        self.log_divider("generation", f"Mutant: {mutant_id}")
        self.log("generation", "AGENT PROMPT:")
        self.log("generation", agent_prompt)

    def log_generation_response(self, mutant_id: str, response: dict):
        self.log("generation", f"\nLLM RESPONSE for {mutant_id}:")
        self.log("generation", json.dumps(response, indent=2, default=str))

    def log_fixing_attempt(self, mutant_id: str, iteration: int, error_message: str):
        self.log_divider("fixing", f"Mutant: {mutant_id} (attempt {iteration})")
        self.log("fixing", "ERROR TO FIX:")
        self.log("fixing", error_message)

    def log_fixing_response(self, mutant_id: str, iteration: int, response: dict):
        self.log("fixing", f"\nFIX RESPONSE (attempt {iteration}):")
        self.log("fixing", json.dumps(response, indent=2, default=str))

    def log_verification(self, mutant_id: str, step: str, passed: bool, output: str):
        status = "PASS" if passed else "FAIL"
        self.log("verification", f"[{status}] {mutant_id} - {step}")
        if not passed or step == "mutant":
            self.log("verification", f"  Output (first 500 chars):\n{output[:500]}")

    def log_summary(self, summary: dict):
        self.log("summary", json.dumps(summary, indent=2, default=str))

    @property
    def dir(self) -> Path:
        return self.run_dir
