import importlib
import logging
import pkgutil
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Type

logger = logging.getLogger(__name__)

AGENT_REGISTRY: Dict[str, Type["BaseAgent"]] = {}


class BaseAgent(ABC):
    """
    Common interface every pipeline step (agent) implements, so agent/core.py can run an
    arbitrary, self-reconfigurable pipeline (settings.json's "pipeline" list) instead of a
    hardcoded call chain. A step reads what it needs from `context` and returns the context
    with its own results merged in.
    """

    name: str = ""
    required_packages: List[str] = []

    def __init__(self, config: Any):
        self.config = config

    @abstractmethod
    def run(self, context: Dict[str, Any]) -> Dict[str, Any]:
        ...


def register_agent(name: str):
    """Class decorator that registers a BaseAgent subclass under `name` in AGENT_REGISTRY."""
    def decorator(cls: Type[BaseAgent]) -> Type[BaseAgent]:
        cls.name = name
        AGENT_REGISTRY[name] = cls
        return cls
    return decorator


def discover_agents(package: str = "agents") -> Dict[str, Type[BaseAgent]]:
    """
    Imports every module under `agents/` so their @register_agent decorators run and populate
    AGENT_REGISTRY. Safe to call repeatedly (e.g. after a new agent file is scaffolded at
    runtime) since already-imported modules are simply reused.
    """
    pkg = importlib.import_module(package)
    for _, module_name, _ in pkgutil.iter_modules(pkg.__path__, prefix=f"{package}."):
        if module_name.endswith(".base"):
            continue
        importlib.import_module(module_name)
    return dict(AGENT_REGISTRY)
