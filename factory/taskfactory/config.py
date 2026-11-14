"""Static configuration for the click task factory.

Everything that changes how a run behaves can be overridden with an environment variable, so a re-run
is reproducible from the README alone. API keys are read by the SDK from the environment and never
appear here.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # factory/

from .env import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

CACHE = Path(os.environ.get("FACTORY_CACHE", ROOT / ".cache"))
TEMPLATES = Path(__file__).resolve().parent / "templates"

# --- the supplied repository -------------------------------------------------------------------
SOURCE_REPO = "pallets/click"
UPSTREAM_URL = "https://github.com/pallets/click.git"
BASE_TAG = "8.5.1"
BASE_COMMIT = "cf19179e8e2fd54c601378cec9e0d21bb12b6fb1"   # tag 8.5.1, released 2026-11-05
UPSTREAM_BRANCH = "main"                                     # novelty is checked against live main
LICENSE = "BSD-3-Clause"
LANGUAGE = "python"
ORG_REPO_SLUG = "pallets__click"

# --- container environment (must match what the local checks use) -----------------------------
PY_IMAGE = "python:3.12-slim-bookworm"
PYTEST_PIN = "pytest==8.4.2"
NETWORK_MODE = os.environ.get("FACTORY_NETWORK_MODE", "public")  # the benchmark agent needs egress

# --- generation model ---------------------------------------------------------------------------
# An OpenRouter key (sk-or-...) placed in ANTHROPIC_API_KEY by mistake is moved to OPENROUTER_API_KEY.
if os.environ.get("ANTHROPIC_API_KEY", "").startswith("sk-or-") and not os.environ.get("OPENROUTER_API_KEY"):
    os.environ["OPENROUTER_API_KEY"] = os.environ.pop("ANTHROPIC_API_KEY")
_default_backend = ("anthropic" if os.environ.get("ANTHROPIC_API_KEY")
                    else "openrouter" if os.environ.get("OPENROUTER_API_KEY") else "anthropic")
BACKEND = os.environ.get("FACTORY_BACKEND", _default_backend)          # "anthropic" | "openrouter"
OPENROUTER_URL = "https://openrouter.ai/api/v1"
MODEL = os.environ.get("FACTORY_MODEL", "claude-opus-5-5" if BACKEND == "anthropic"
                       else "anthropic/claude-opus-5.5")
EFFORT = os.environ.get("FACTORY_EFFORT", "high")
MAX_OUTPUT_TOKENS = int(os.environ.get("FACTORY_MAX_TOKENS", "64000"))
MAX_ATTEMPTS = int(os.environ.get("FACTORY_MAX_ATTEMPTS", "3"))      # implement + repairs per task
# Hard cap on generation spend (USD) for one output directory: it counts the calls logged under that
# directory's runs/ folder, never the key's lifetime usage. A call starts only if its worst case fits.
BUDGET_USD = float(os.environ.get("FACTORY_BUDGET_USD", "25"))
RESERVE_PER_CALL_USD = float(os.environ.get("FACTORY_RESERVE_PER_CALL", "1.5"))
RESPONSE_CACHE = os.environ.get("FACTORY_NO_CACHE", "") == ""        # reuse identical earlier calls
OFFLINE = os.environ.get("FACTORY_OFFLINE", "") not in ("", "0")     # never call a model; replay cache only
CACHE_STAGGER_SEC = float(os.environ.get("FACTORY_CACHE_STAGGER", "60"))

# --- solver jury: measure difficulty before a task leaves the factory -------------------------------
JURY = os.environ.get("FACTORY_JURY", "1") != "0"
JURY_WEAK_MODEL = os.environ.get("FACTORY_JURY_WEAK", "anthropic/claude-haiku-5.5")   # the benchmark model
JURY_WEAK_EFFORT = os.environ.get("FACTORY_JURY_WEAK_EFFORT", "high")
JURY_K = int(os.environ.get("FACTORY_JURY_K", "3"))                   # one-shot attempts per round
JURY_MAX_WEAK_RATE = float(os.environ.get("FACTORY_JURY_MAX_RATE", "0.34"))   # hard enough: solved/k <= this
JURY_MAX_ESCALATIONS = int(os.environ.get("FACTORY_JURY_ESCALATIONS", "2"))   # "deepen" rounds per task
JURY_STRONG_MODEL = os.environ.get("FACTORY_JURY_STRONG", "")         # optional blind strong solver; "" = off
JURY_CONTEXT_CHARS = int(os.environ.get("FACTORY_JURY_CONTEXT", "240000"))    # keeps prompts < 100k tokens
JURY_MAX_TRAPS = 20                                                    # existing tests a weak patch broke


def backend_model(model: str) -> str:
    """Model id in the active backend's spelling (OpenRouter: anthropic/claude-haiku-5.5; SDK: claude-haiku-5-5)."""
    if BACKEND == "anthropic" and model.startswith("anthropic/"):
        return model.removeprefix("anthropic/").replace(".", "-")
    if BACKEND == "openrouter" and model.startswith("claude-"):
        head, _, tail = model.rpartition("-")
        return f"anthropic/{head}.{tail}"
    return model

# USD per million tokens, used only for the cost column in pipeline_run.json.
PRICES = {
    "claude-opus-5-5": {"in": 4.00, "out": 20.00, "cache_read": 0.20, "cache_write": 5.00},
    "claude-sonnet-5-5": {"in": 2.00, "out": 10.00, "cache_read": 0.10, "cache_write": 2.50},
    "claude-haiku-5-5": {"in": 0.10, "out": 0.50, "cache_read": 0.01, "cache_write": 0.125},
}


def price_for(model: str) -> dict:
    key = model.removeprefix("anthropic/").replace(".", "-")
    return PRICES.get(key, PRICES["claude-opus-5-5"])

# --- gate thresholds ----------------------------------------------------------------------------
MIN_F2P_TESTS = 6            # a task needs at least this many feature tests
MAX_P2P_TESTS = 80           # curated regression slice, not a whole-suite dump
MIN_P2P_TESTS = 8
MUTANTS_PER_TASK = 12        # mutants sampled from the gold's added lines
MIN_MUTATION_KILL = 0.7      # fraction of compilable mutants the feature tests must catch
TEST_TIMEOUT_SEC = 600

# --- local paths ----------------------------------------------------------------------------------
CLONE_DIR = CACHE / "click"
BASE_SNAPSHOT = CACHE / "snap-base"
MAIN_SNAPSHOT = CACHE / "snap-main"
WORK_DIR = CACHE / "work"
COVMAP_FILE = CACHE / "covmap.json"
BASE_HEALTH_FILE = CACHE / "base_health.json"
LLM_LOG_DIR = ROOT / "runs"
