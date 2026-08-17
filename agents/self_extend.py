import importlib
import importlib.util
import json
import logging
import os
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from config import Config

logger = logging.getLogger(__name__)

# Packages every module needs regardless of which pipeline steps are enabled.
CORE_REQUIRED_PACKAGES = ["openai", "requests"]

# Maps an importable module name to the pip package name, where they differ.
_PIP_NAME_OVERRIDES = {
    "yaml": "PyYAML",
    "bs4": "beautifulsoup4",
    "langchain_openai": "langchain-openai",
    "langchain_core": "langchain-core",
}

REQUIREMENTS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "requirements.txt")
STATE_PATH_DEFAULT = "session_state.json"


@dataclass
class SessionState:
    """
    A checkpoint of an in-progress run, persisted to <output_dir>/session_state.json.

    Written before any self-reconfiguration action (installing a package, scaffolding a new
    agent) so that if the process has to restart (pip installs are only reliably picked up by
    a fresh interpreter), it can resume from `current_query_index` / `results` instead of
    starting the target_queries list over. Also written after every completed query as a
    lightweight progress checkpoint, independent of any install/scaffold activity.
    """
    run_id: str
    target_queries: List[str]
    current_query_index: int = 0
    results: List[Dict[str, Any]] = field(default_factory=list)
    pending_gap_actions: List[str] = field(default_factory=list)
    updated_at: float = field(default_factory=time.time)

    @staticmethod
    def _path(config: Config) -> str:
        return os.path.join(config.OUTPUT_DIR, STATE_PATH_DEFAULT)

    @classmethod
    def load(cls, config: Config) -> Optional["SessionState"]:
        path = cls._path(config)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r") as f:
                data = json.load(f)
            return cls(**data)
        except (json.JSONDecodeError, OSError, TypeError) as e:
            logger.warning(f"Failed to load session checkpoint at {path}: {e}. Starting fresh.")
            return None

    def save(self, config: Config) -> None:
        self.updated_at = time.time()
        path = self._path(config)
        tmp_path = f"{path}.tmp"
        with open(tmp_path, "w") as f:
            json.dump(asdict(self), f, indent=2)
        os.replace(tmp_path, path)  # atomic on POSIX, avoids a half-written checkpoint

    def clear(self, config: Config) -> None:
        path = self._path(config)
        if os.path.exists(path):
            os.remove(path)

    @classmethod
    def new(cls, target_queries: List[str]) -> "SessionState":
        return cls(run_id=str(uuid.uuid4()), target_queries=list(target_queries))


def _pip_name(module_name: str) -> str:
    return _PIP_NAME_OVERRIDES.get(module_name, module_name)


def check_missing_packages(required_packages: List[str]) -> List[str]:
    """Returns the subset of `required_packages` (import names) that aren't importable."""
    missing = []
    for pkg in dict.fromkeys(required_packages):  # de-dupe, preserve order
        if importlib.util.find_spec(pkg) is None:
            missing.append(pkg)
    return missing


def install_missing_packages(missing: List[str], config: Config, state: SessionState) -> None:
    """
    Installs missing packages into the current interpreter's environment, checkpointing state
    first so a restart (needed because pip installs into an already-imported interpreter aren't
    reliably importable without one) can resume exactly where this run left off.
    """
    if not missing:
        return

    pip_names = [_pip_name(m) for m in missing]
    logger.warning(f"Missing packages detected: {pip_names}. Checkpointing before install.")

    state.pending_gap_actions = [f"install:{name}" for name in pip_names]
    state.save(config)

    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-q", *pip_names],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        logger.error(f"pip install failed for {pip_names}: {result.stderr}")
        raise RuntimeError(f"Failed to install required packages {pip_names}: {result.stderr}")

    _append_requirements(pip_names)

    still_missing = check_missing_packages(missing)
    if still_missing:
        raise RuntimeError(f"Packages installed but still not importable: {still_missing}")

    logger.info(f"Installed {pip_names}. Restarting process to load them cleanly.")
    state.pending_gap_actions = []
    state.save(config)
    _reexec()


def _append_requirements(pip_names: List[str]) -> None:
    existing = set()
    if os.path.exists(REQUIREMENTS_PATH):
        with open(REQUIREMENTS_PATH, "r") as f:
            existing = {line.split(">=")[0].split("==")[0].strip().lower()
                        for line in f if line.strip()}
    new_lines = [name for name in pip_names if name.lower() not in existing]
    if new_lines:
        with open(REQUIREMENTS_PATH, "a") as f:
            for name in new_lines:
                f.write(f"{name}\n")


def _reexec() -> None:
    """Replaces the current process image with a fresh interpreter running the same command."""
    os.execv(sys.executable, [sys.executable] + sys.argv)


AGENT_SCAFFOLD_PROMPT = """You are extending a Python SaaS-problem-solving pipeline. Every \
pipeline step is a subclass of `agents.base.BaseAgent` registered with `@register_agent(name)`.

BaseAgent contract:
    class BaseAgent(ABC):
        name: str
        required_packages: List[str]
        def __init__(self, config): self.config = config
        def run(self, context: Dict[str, Any]) -> Dict[str, Any]: ...

`context` is a dict threaded through the pipeline; earlier steps may have set 'topic', \
'search_queries', 'search_results', 'problems', 'solutions'. Your step is named "{name}". \
Write a single, complete Python module implementing one `@register_agent("{name}")` class \
(pick a sensible class name) that:
  - reads whatever it needs from `context`
  - performs its task (use `self.config.OPENAI_API_KEY` and the `openai` package for any LLM \
call it needs; do not invent config fields that don't exist)
  - writes its own result into `context` under a clear new key
  - returns `context`

Output ONLY the raw Python source code for the module (imports included: at minimum \
`from agents.base import BaseAgent, register_agent` and `from typing import Any, Dict`), no \
explanation, no markdown fences.
"""


def ensure_agent(name: str, config: Config, state: SessionState) -> None:
    """
    Called when settings.json's pipeline references a step `name` with no registered agent.
    Checkpoints state, then asks the LLM to draft a new agents/{name}_agent.py implementing
    BaseAgent, writes it to disk, and hot-imports it. New files don't require a process
    restart (only pip installs into an already-imported interpreter do), so this returns
    without re-exec'ing.
    """
    from openai import OpenAI
    from agents.base import discover_agents

    logger.warning(f"No agent registered for pipeline step '{name}'. Scaffolding one.")
    state.pending_gap_actions = [f"scaffold:{name}"]
    state.save(config)

    client = OpenAI(api_key=config.OPENAI_API_KEY)
    response = client.chat.completions.create(
        model=config.LLM_MODEL_NAME,
        messages=[
            {"role": "system", "content": AGENT_SCAFFOLD_PROMPT.format(name=name)},
            {"role": "user", "content": f"Generate the '{name}' pipeline step module."},
        ],
        temperature=0.2,
    )
    source = response.choices[0].message.content or ""
    source = source.strip().strip("`")
    if source.lower().startswith("python\n"):
        source = source[len("python\n"):]

    module_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"{name}_agent.py")
    with open(module_path, "w") as f:
        f.write(source)
    logger.info(f"Wrote scaffolded agent module to {module_path}")

    discover_agents()  # re-scan agents/ so the new file's @register_agent call runs

    state.pending_gap_actions = []
    state.save(config)
