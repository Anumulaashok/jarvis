import logging
from typing import Any, Dict, List, Optional

from config import Config
from agents.base import AGENT_REGISTRY, discover_agents
from agents.self_extend import SessionState, ensure_agent

logger = logging.getLogger(__name__)


class AgentCore:
    """
    Runs config.PIPELINE (default: search -> analyze -> solve -> monetize) as a sequence of
    BaseAgent steps looked up from the plugin registry. The pipeline order and membership are
    read from settings.json (via Config), so re-editing settings.json and reconfiguring is
    enough to change what runs, without touching this file.

    If a pipeline step name has no registered agent, self_extend.ensure_agent() checkpoints the
    current session and scaffolds one at runtime before continuing.
    """

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        discover_agents()

    def _get_step(self, step_name: str, state: SessionState):
        step_cls = AGENT_REGISTRY.get(step_name)
        if step_cls is None:
            ensure_agent(step_name, self.config, state)
            step_cls = AGENT_REGISTRY.get(step_name)
        if step_cls is None:
            raise RuntimeError(f"Pipeline step '{step_name}' could not be resolved or scaffolded.")
        return step_cls(self.config)

    def identify_and_solve(
        self, target_saas_domain_or_keywords: str, state: Optional[SessionState] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Runs config.PIPELINE for a single topic/keyword string and returns a structured report,
        or None if no problems/solutions resulted. `state`, if provided, is checkpointed before
        any self-reconfiguration action triggered mid-pipeline (missing agent scaffolding).
        """
        topic = target_saas_domain_or_keywords
        logger.info(f"Starting pipeline {self.config.PIPELINE} for topic: '{topic}'")

        state = state or SessionState.new([topic])
        context: Dict[str, Any] = {"topic": topic}

        for step_name in self.config.PIPELINE:
            step = self._get_step(step_name, state)
            logger.info(f"Running pipeline step '{step_name}'...")
            context = step.run(context)

        problems: List[Dict[str, Any]] = context.get("problems", [])
        solutions: List[Dict[str, Any]] = context.get("solutions", [])

        if not problems or not solutions:
            logger.warning(f"No problems/solutions produced for '{topic}'.")
            return None

        primary_solution = solutions[0]
        primary_problem = next(
            (p for p in problems if p.get("id") == primary_solution.get("problem_id")),
            problems[0],
        )
        primary_monetization = primary_solution.get("monetization") or {}
        monetization_summary = "; ".join(
            f"{s.get('model', 'N/A')}: {s.get('value_proposition', 'N/A')}"
            for s in primary_monetization.get("monetization_strategies", [])
        ) or "No monetization strategy generated."

        return {
            "target_area": topic,
            "search_queries": context.get("search_queries", []),
            "problem_statement": primary_problem.get("summary", "N/A"),
            "solution_summary": primary_solution.get("description", "N/A"),
            "monetization_strategy": monetization_summary,
            "details": (
                f"Identified {len(problems)} problem(s) and {len(solutions)} solution(s) for "
                f"'{topic}'. Primary solution: '{primary_solution.get('solution_name', 'N/A')}'."
            ),
            "problems": problems,
            "solutions": solutions,
        }
