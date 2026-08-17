import json
import os
from typing import Any, Dict

SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")

_DEFAULT_SETTINGS: Dict[str, Any] = {
    "llm": {
        "model_name": "gpt-4-turbo-preview",
        "temperature": 0.7,
        "max_tokens": 4096,
        "top_p": 1.0,
    },
    "search": {"results_limit": 10, "timeout_seconds": 30},
    "analysis": {"min_problem_evidence_length": 50, "chunk_overlap": 200},
    "solution": {"min_description_length": 100},
    "output_dir": "output",
    "log_level": "INFO",
    "pipeline": ["search", "analyze", "solve", "monetize"],
    "target_queries": [],
}


class Config:
    """
    Project-wide configuration for the SaaS problem identification agent.

    Tunable runtime knobs (model choice, thresholds, the agent pipeline, target queries)
    live in settings.json and are loaded fresh on every Config() construction, so the agent
    can rewrite settings.json at runtime (via save_settings()) and have the change take effect
    on its next reconfigure/restart cycle. Secrets (API keys) always come from the environment
    and are never persisted to settings.json. Prompts are treated as code, not settings, and
    stay as class constants below.
    """

    # --- API Keys (never written to disk) ---
    SERPER_API_KEY: str = os.getenv("SERPER_API_KEY", "")
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")

    # --- Prompts (code, not runtime-tunable settings) ---
    SEARCH_QUERY_GENERATION_PROMPT: str = (
        "You are an expert at generating precise search queries to find real-world problems. "
        "Given a broad topic, generate 3-5 distinct search queries that are likely to uncover "
        "common pain points, issues, or unmet needs related to that topic. "
        "Focus on practical problems that could potentially be solved by a SaaS product. "
        "Return only the queries, one per line."
    )
    PROBLEM_ANALYSIS_PROMPT: str = (
        "You are an expert at identifying real-world problems from web search results. "
        "Analyze the following search results to identify and categorize real-world problems, "
        "pain points, and unmet needs. For each identified problem, provide a concise 'summary', "
        "its potential 'impact' (who is affected and how), and 'evidence' (a quote or paraphrased "
        "detail from the text supporting the problem, at least a full sentence long). "
        "Structure the output clearly. Focus on problems that could plausibly be solved by a SaaS "
        "product. Return a JSON object with a single key 'problems' containing a list of these "
        "problem objects, each with keys 'summary', 'impact', and 'evidence'."
    )
    SOLUTION_PROPOSAL_PROMPT: str = (
        "Based on the identified problems, propose a viable SaaS solution for each. "
        "For each solution, describe its core functionality, target audience, "
        "key features, and how it addresses the identified pain points. "
        "Return a JSON object with a list of solutions."
    )
    # Used by SolutionAgent.propose_solutions(). Must yield JSON matching the schema consumed
    # downstream by MonetizationAgent (solution_name/description/target_users).
    SOLUTION_GENERATION_PROMPT: str = (
        "You are an expert SaaS solution architect. Propose one viable, innovative SaaS solution "
        "for the real-world problem described below.\n\n"
        "Problem summary: {problem_summary}\n"
        "Impact: {problem_impact}\n"
        "Evidence: {problem_evidence}\n\n"
        "Return a JSON object with exactly these keys:\n"
        "  - 'solution_name': a short, memorable product name\n"
        "  - 'description': a 2-4 sentence explanation of what the product does\n"
        "  - 'target_users': who the product is built for\n"
        "  - 'core_problem_addressed': a one-sentence restatement of the pain point it solves\n"
        "  - 'key_features': a list of 3-6 concrete features\n"
        "  - 'value_proposition': why a customer would choose this over alternatives\n"
        "  - 'differentiation': what makes this solution distinct from existing tools"
    )
    MONETIZATION_STRATEGY_PROMPT: str = (
        "You are an expert in SaaS business models and pricing strategy. Given a proposed SaaS "
        "solution, outline 2-3 distinct, detailed monetization strategies (e.g. subscription, "
        "freemium, usage-based, transactional, hybrid). For each strategy, include:\n"
        "  - 'model': the pricing model type\n"
        "  - 'description': how the model works for this specific product\n"
        "  - 'pricing_tiers': a list of objects with 'name', 'features' (list), and 'price'\n"
        "  - 'value_proposition': why this strategy fits the target users and maximizes revenue\n"
        "Return a JSON object with a key 'monetization_strategies' containing a list of these "
        "strategy objects."
    )

    def __init__(self) -> None:
        settings = self._load_settings()

        llm = settings.get("llm", {})
        self.LLM_MODEL_NAME: str = os.getenv("LLM_MODEL_NAME", llm.get("model_name", "gpt-4-turbo-preview"))
        self.LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", llm.get("temperature", 0.7)))
        self.LLM_MAX_TOKENS: int = int(os.getenv("LLM_MAX_TOKENS", llm.get("max_tokens", 4096)))
        self.LLM_TOP_P: float = float(os.getenv("LLM_TOP_P", llm.get("top_p", 1.0)))

        search = settings.get("search", {})
        self.SEARCH_RESULTS_LIMIT: int = int(os.getenv("SEARCH_RESULTS_LIMIT", search.get("results_limit", 10)))
        self.SEARCH_TIMEOUT_SECONDS: int = int(os.getenv("SEARCH_TIMEOUT_SECONDS", search.get("timeout_seconds", 30)))

        analysis = settings.get("analysis", {})
        self.MIN_PROBLEM_EVIDENCE_LENGTH: int = int(analysis.get("min_problem_evidence_length", 50))
        self.LLM_CHUNK_OVERLAP: int = int(analysis.get("chunk_overlap", 200))

        solution = settings.get("solution", {})
        self.MIN_SOLUTION_DESCRIPTION_LENGTH: int = int(solution.get("min_description_length", 100))

        self.OUTPUT_DIR: str = os.getenv("OUTPUT_DIR", settings.get("output_dir", "output"))
        self.LOG_LEVEL: str = os.getenv("LOG_LEVEL", settings.get("log_level", "INFO"))

        # Self-reconfigurable: which agent steps run, in what order, and what topics to chase.
        self.PIPELINE: list = list(settings.get("pipeline", ["search", "analyze", "solve", "monetize"]))
        self.TARGET_QUERIES: list = list(settings.get("target_queries", []))

        self._raw_settings = settings

        if not self.SERPER_API_KEY:
            print("WARNING: SERPER_API_KEY is not set. Web search functionality will be limited or unavailable.")
        if not self.OPENAI_API_KEY:
            print("WARNING: OPENAI_API_KEY is not set. LLM functionality will be limited or unavailable.")

        os.makedirs(self.OUTPUT_DIR, exist_ok=True)

    @staticmethod
    def _load_settings() -> Dict[str, Any]:
        if not os.path.exists(SETTINGS_PATH):
            return dict(_DEFAULT_SETTINGS)
        try:
            with open(SETTINGS_PATH, "r") as f:
                loaded = json.load(f)
            merged = dict(_DEFAULT_SETTINGS)
            merged.update(loaded)
            return merged
        except (json.JSONDecodeError, OSError) as e:
            print(f"WARNING: Failed to read settings.json ({e}). Falling back to defaults.")
            return dict(_DEFAULT_SETTINGS)

    def save_settings(self, updates: Dict[str, Any]) -> None:
        """
        Persists a partial update to settings.json (shallow-merged at the top level) so the
        agent can reconfigure its own runtime behavior. Does not touch API keys, which always
        come from the environment. Callers should re-instantiate Config() after saving to pick
        up the new values (or trigger a process restart if the change requires one, e.g. a
        newly installed dependency).
        """
        current = dict(self._raw_settings)
        current.update(updates)
        current.pop("SERPER_API_KEY", None)
        current.pop("OPENAI_API_KEY", None)
        with open(SETTINGS_PATH, "w") as f:
            json.dump(current, f, indent=2)
        self._raw_settings = current

    def get_llm_config(self) -> Dict[str, Any]:
        return {
            "model": self.LLM_MODEL_NAME,
            "temperature": self.LLM_TEMPERATURE,
            "max_tokens": self.LLM_MAX_TOKENS,
            "top_p": self.LLM_TOP_P,
        }

    def __str__(self) -> str:
        return (
            f"Config(\n"
            f"  SERPER_API_KEY_SET={bool(self.SERPER_API_KEY)},\n"
            f"  OPENAI_API_KEY_SET={bool(self.OPENAI_API_KEY)},\n"
            f"  LLM_MODEL_NAME='{self.LLM_MODEL_NAME}',\n"
            f"  LLM_TEMPERATURE={self.LLM_TEMPERATURE},\n"
            f"  LLM_MAX_TOKENS={self.LLM_MAX_TOKENS},\n"
            f"  SEARCH_RESULTS_LIMIT={self.SEARCH_RESULTS_LIMIT},\n"
            f"  PIPELINE={self.PIPELINE},\n"
            f"  OUTPUT_DIR='{self.OUTPUT_DIR}',\n"
            f"  LOG_LEVEL='{self.LOG_LEVEL}'\n"
            f")"
        )


if __name__ == "__main__":
    print("Loading configuration...")
    config = Config()
    print(config)
    print("\nLLM Configuration:")
    print(config.get_llm_config())
