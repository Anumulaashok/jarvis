import json
import logging
from typing import Dict, List, Any

# Project imports
from config import Config
from utils.helpers import clean_text
from agents.base import BaseAgent, register_agent

# Third-party imports (assuming openai is installed)
import openai

# Set up logging for the agent
logger = logging.getLogger(__name__)

class SolutionAgent:
    """
    Agent responsible for proposing viable SaaS solutions based on analyzed real-world problems.

    This agent takes structured problem data, uses an LLM to brainstorm and refine
    SaaS solution ideas, and outputs detailed proposals in a structured JSON format.
    """

    def __init__(self, config: Config):
        """
        Initializes the SolutionAgent with the provided configuration.

        Args:
            config (Config): The project configuration object.
        """
        self.config = config
        self.llm_client = self._initialize_llm_client()
        logger.info("SolutionAgent initialized.")

    def _initialize_llm_client(self):
        """
        Initializes and returns an OpenAI client using the API key from configuration.

        Raises:
            ValueError: If OPENAI_API_KEY is not set in the configuration.

        Returns:
            openai.OpenAI: An initialized OpenAI client.
        """
        if not self.config.OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY is not set in configuration for SolutionAgent.")
        
        # Instantiate the OpenAI client
        return openai.OpenAI(api_key=self.config.OPENAI_API_KEY)

    def propose_solutions(self, analyzed_problems: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Proposes SaaS solutions for a list of analyzed real-world problems.

        This method iterates through each problem, constructs a prompt using problem details,
        sends it to the LLM, and parses the JSON response to extract solution details.

        Args:
            analyzed_problems (List[Dict[str, Any]]): A list of dictionaries, where each
                                                       dictionary represents an analyzed problem,
                                                       typically containing at least 'summary', 'impact',
                                                       and 'evidence'. An 'id' field is also expected
                                                       for traceability.

        Returns:
            List[Dict[str, Any]]: A list of proposed SaaS solutions. Each solution is a dictionary
                                  with keys like 'solution_name', 'description', 'target_audience',
                                  'core_problem_addressed', 'key_features', 'value_proposition',
                                  'differentiation', and 'problem_id' for linking back to the original problem.
                                  Returns an empty list if no solutions can be proposed or on error.
        """
        if not analyzed_problems:
            logger.warning("No analyzed problems provided to SolutionAgent. Returning empty list.")
            return []

        solutions_list: List[Dict[str, Any]] = []

        for problem in analyzed_problems:
            # Safely get problem details, providing default empty strings
            problem_summary = problem.get("summary", "No summary provided.")
            problem_impact = problem.get("impact", "Unknown impact.")
            problem_evidence = problem.get("evidence", "No evidence provided.")
            problem_id = problem.get("id", "UNKNOWN_PROBLEM_ID") # Default ID for traceability

            logger.info(f"Attempting to generate solution for problem ID: {problem_id} - {problem_summary[:70]}...")

            try:
                # Use clean_text helper to prepare inputs for the LLM prompt
                # The SOLUTION_GENERATION_PROMPT from Config is expected to contain placeholders
                # for problem_summary, problem_impact, and problem_evidence, and define the JSON structure.
                formatted_prompt = self.config.SOLUTION_GENERATION_PROMPT.format(
                    problem_summary=clean_text(problem_summary),
                    problem_impact=clean_text(problem_impact),
                    problem_evidence=clean_text(problem_evidence)
                )

                messages = [
                    {"role": "system", "content": "You are an expert SaaS solution architect. Your task is to propose innovative and viable SaaS solutions for real-world problems. Ensure the solution is practical, addresses the core pain points, and outlines key features. Your output MUST be a JSON object, strictly following the specified schema provided in the user prompt."},
                    {"role": "user", "content": formatted_prompt}
                ]

                # Make the LLM call
                response = self.llm_client.chat.completions.create(
                    model=self.config.LLM_MODEL_NAME,
                    messages=messages,
                    temperature=self.config.LLM_TEMPERATURE,
                    max_tokens=self.config.LLM_MAX_TOKENS,
                    top_p=self.config.LLM_TOP_P,
                    response_format={"type": "json_object"} # Crucial for instructing the LLM to return JSON
                )

                response_content = response.choices[0].message.content
                if not response_content:
                    logger.warning(f"LLM returned empty content for problem {problem_id}. Skipping.")
                    continue

                solution_data = json.loads(response_content)

                # Ensure the parsed data is a dictionary and add problem_id for context
                if isinstance(solution_data, dict):
                    solution_data['problem_id'] = problem_id
                    solutions_list.append(solution_data)
                    logger.info(f"Successfully generated solution for problem {problem_id}: {solution_data.get('solution_name', 'Unnamed Solution')}")
                elif isinstance(solution_data, list):
                    # Handle cases where LLM might return a list of solutions for a single problem
                    for sol in solution_data:
                        if isinstance(sol, dict):
                            sol['problem_id'] = problem_id
                            solutions_list.append(sol)
                    logger.info(f"Successfully generated {len(solution_data)} solutions for problem {problem_id}.")
                else:
                    logger.error(f"LLM response for problem {problem_id} was not a dictionary or list, type: {type(solution_data).__name__}. Content: {response_content[:200]}")

            except openai.APIError as e:
                logger.error(f"OpenAI API error while proposing solution for problem {problem_id}: {e}", exc_info=True)
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse JSON response from LLM for problem {problem_id}: {e}\nRaw Response: {response_content[:500]}", exc_info=True)
            except Exception as e:
                logger.error(f"An unexpected error occurred while proposing solution for problem {problem_id}: {e}", exc_info=True)

        logger.info(f"SolutionAgent finished. Generated a total of {len(solutions_list)} SaaS solutions.")
        return solutions_list


@register_agent("solve")
class SolveStep(BaseAgent):
    """
    Pipeline step: turns context['problems'] into context['solutions'] via SolutionAgent.
    """
    required_packages = ["openai"]

    def __init__(self, config: Config):
        super().__init__(config)
        self.impl = SolutionAgent(config)

    def run(self, context: Dict[str, Any]) -> Dict[str, Any]:
        solutions = self.impl.propose_solutions(context.get("problems", []))
        for solution in solutions:
            solution.setdefault("solution_name", solution.get("title", "Unnamed Solution"))
            solution.setdefault("target_users", solution.get("target_audience", "Not specified"))
        context["solutions"] = solutions
        return context