from typing import Dict, Any, List
import json
import logging

from config import Config
from utils.helpers import clean_text
from openai import OpenAI
from agents.base import BaseAgent, register_agent

logger = logging.getLogger(__name__)

class MonetizationAgent:
    """
    Agent responsible for outlining potential monetization strategies for proposed SaaS solutions.

    This agent takes a proposed SaaS solution and uses an LLM to generate
    relevant monetization models, pricing tiers, and value propositions.
    """

    def __init__(self, config: Config):
        """
        Initializes the MonetizationAgent with a configuration object.

        Args:
            config (Config): The project-wide configuration settings.
        """
        self.config = config
        self.llm_client = OpenAI(api_key=self.config.OPENAI_API_KEY)
        self._validate_config()

    def _validate_config(self):
        """
        Validates the necessary configuration settings are present.
        """
        if not self.config.OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY is not set in Config.")
        if not self.config.MONETIZATION_STRATEGY_PROMPT:
            raise ValueError("MONETIZATION_STRATEGY_PROMPT is not set in Config.")

    def generate_monetization_strategies(self, solution_proposal: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generates potential monetization strategies for a given SaaS solution proposal.

        Args:
            solution_proposal (Dict[str, Any]): A dictionary containing the proposed SaaS solution,
                                                 including its name, description, and target users.
                                                 Expected keys: 'solution_name', 'description', 'target_users'.

        Returns:
            Dict[str, Any]: A dictionary outlining monetization strategies, including models,
                            pricing tiers, and value propositions.
                            Example structure:
                            {
                                "solution_name": "SaaS Solution Name",
                                "monetization_strategies": [
                                    {
                                        "model": "Subscription",
                                        "description": "Monthly/annual recurring fees.",
                                        "pricing_tiers": [
                                            {"name": "Basic", "features": ["Feature A", "Feature B"], "price": "$X/month"},
                                            {"name": "Pro", "features": ["Feature A", "Feature B", "Feature C"], "price": "$Y/month"}
                                        ],
                                        "value_proposition": "Access to essential features..."
                                    }
                                    // ... other models
                                ]
                            }
        Raises:
            ValueError: If required keys are missing from solution_proposal.
            Exception: For issues with LLM API calls.
        """
        required_keys = ['solution_name', 'description', 'target_users']
        if not all(key in solution_proposal for key in required_keys):
            missing_keys = [key for key in required_keys if key not in solution_proposal]
            raise ValueError(f"Missing required keys in solution_proposal: {', '.join(missing_keys)}")

        solution_name = solution_proposal.get('solution_name', 'Unnamed Solution')
        solution_description = solution_proposal.get('description', 'No description provided.')
        target_users = solution_proposal.get('target_users', 'Undefined target users.')

        prompt_messages = [
            {"role": "system", "content": self.config.MONETIZATION_STRATEGY_PROMPT},
            {"role": "user", "content": (
                f"SaaS Solution Name: {solution_name}\n"
                f"Description: {solution_description}\n"
                f"Target Users: {target_users}\n\n"
                "Please generate monetization strategies in JSON format as specified in your system prompt."
            )}
        ]

        try:
            response = self.llm_client.chat.completions.create(
                model=self.config.LLM_MODEL_NAME,
                messages=prompt_messages,
                temperature=self.config.LLM_TEMPERATURE,
                max_tokens=self.config.LLM_MAX_TOKENS,
                top_p=self.config.LLM_TOP_P,
                response_format={"type": "json_object"}
            )
            raw_content = response.choices[0].message.content
            cleaned_content = clean_text(raw_content)
            monetization_data = json.loads(cleaned_content)
            logger.info(f"Successfully generated monetization strategies for '{solution_name}'.")
            
            # Add the solution name back to the output structure for context
            monetization_data['solution_name'] = solution_name

            return monetization_data

        except json.JSONDecodeError as e:
            logger.error(f"Failed to decode JSON from LLM response for '{solution_name}': {e}")
            logger.error(f"Raw LLM response: {raw_content}")
            raise Exception(f"Invalid JSON response from LLM for solution '{solution_name}'.") from e
        except Exception as e:
            logger.error(f"Error generating monetization strategies for '{solution_name}': {e}")
            raise

@register_agent("monetize")
class MonetizeStep(BaseAgent):
    """
    Pipeline step: attaches a 'monetization' payload to each solution in context['solutions'].
    A single solution's monetization failure is logged and skipped so it doesn't kill the run.
    """
    required_packages = ["openai"]

    def __init__(self, config: Config):
        super().__init__(config)
        self.impl = MonetizationAgent(config)

    def run(self, context: Dict[str, Any]) -> Dict[str, Any]:
        for solution in context.get("solutions", []):
            try:
                solution["monetization"] = self.impl.generate_monetization_strategies(solution)
            except Exception as e:
                logger.error(f"Failed to generate monetization strategy for "
                             f"'{solution.get('solution_name')}': {e}")
                solution["monetization"] = None
        return context


if __name__ == "__main__":
    # This is a placeholder for local testing.
    # In a real scenario, the config would be loaded properly and agents instantiated.
    logging.basicConfig(level=logging.INFO)
    
    # Extend Config temporarily for standalone test if needed
    class TestConfig(Config):
        OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "sk-test-12345") # Use a dummy key for local test if not set
        MONETIZATION_STRATEGY_PROMPT: str = (
            "You are an expert in SaaS business models and monetization. "
            "Given a SaaS solution, outline 2-3 distinct potential monetization strategies. "
            "For each strategy, include: 'model' (e.g., Subscription, Freemium, Transaction-based), "
            "'description', 'pricing_tiers' (a list of dictionaries with 'name', 'features', 'price'), "
            "and a 'value_proposition' for why that strategy is suitable. "
            "Return a JSON object with a key 'monetization_strategies' which is a list of these strategy objects."
        )

    test_config = TestConfig()

    # Mock an OpenAI client for testing without actual API calls if no API key
    if test_config.OPENAI_API_KEY == "sk-test-12345":
        logger.warning("Using a dummy OpenAI API key for local testing. No actual LLM call will be made.")
        class MockChatCompletion:
            def __init__(self, content: str):
                self.content = content
            
            @property
            def message(self):
                class MockMessage:
                    def __init__(self, content: str):
                        self.content = content
                    content: str
                return MockMessage(self.content)

        class MockResponse:
            def __init__(self, content: str):
                self.choices = [MockChatCompletion(content)]

        class MockChat:
            def completions(self):
                return self
            def create(self, *args, **kwargs):
                solution_name = "Unknown Solution"
                for msg in kwargs.get('messages', []):
                    if msg['role'] == 'user':
                        if "SaaS Solution Name:" in msg['content']:
                            solution_name = msg['content'].split("SaaS Solution Name:")[1].split("\n")[0].strip()
                        break

                example_response = {
                    "monetization_strategies": [
                        {
                            "model": "Subscription",
                            "description": "Users pay a recurring fee for access to the platform's features.",
                            "pricing_tiers": [
                                {"name": "Basic", "features": ["Core features", "Limited storage"], "price": "$19/month"},
                                {"name": "Pro", "features": ["All basic features", "Advanced analytics", "More storage"], "price": "$49/month"}
                            ],
                            "value_proposition": f"Consistent access to essential tools for '{solution_name}'."
                        },
                        {
                            "model": "Freemium",
                            "description": "Offer basic features for free, charge for premium features or increased limits.",
                            "pricing_tiers": [
                                {"name": "Free", "features": ["Basic reporting", "Limited user seats"], "price": "Free"},
                                {"name": "Premium", "features": ["All free features", "Custom dashboards", "Unlimited users"], "price": "$79/month"}
                            ],
                            "value_proposition": f"Try before you buy, convert free users to paying customers for advanced '{solution_name}' functionality."
                        }
                    ]
                }
                return MockResponse(json.dumps(example_response))

        class MockOpenAI:
            def __init__(self, api_key: str):
                pass
            @property
            def chat(self):
                return MockChat()

        MonetizationAgent.llm_client = MockOpenAI(api_key="dummy_key")


    monetization_agent = MonetizationAgent(test_config)

    sample_solution = {
        "solution_name": "AI-Powered Meeting Summarizer",
        "description": "A SaaS platform that uses AI to transcribe, summarize, and extract action items from online meetings.",
        "target_users": "Remote teams, project managers, executive assistants"
    }

    try:
        strategies = monetization_agent.generate_monetization_strategies(sample_solution)
        print("\nGenerated Monetization Strategies:")
        print(json.dumps(strategies, indent=2))

        # Test with missing key
        invalid_solution = {
            "solution_name": "Incomplete Solution",
            "description": "This one is missing target users."
        }
        try:
            monetization_agent.generate_monetization_strategies(invalid_solution)
        except ValueError as e:
            print(f"\nCaught expected error for invalid solution: {e}")

    except Exception as e:
        logger.error(f"An error occurred during standalone test: {e}")