import json
import logging
import uuid
from typing import Dict, List, Any, Optional

from config import Config
from utils.helpers import clean_text, chunk_text
from agents.base import BaseAgent, register_agent
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.exceptions import OutputParserException

logger = logging.getLogger(__name__)

class AnalyzerAgent:
    """
    The AnalyzerAgent processes raw search results to identify, categorize, and structure
    real-world problems and pain points. It uses a Large Language Model (LLM) to perform
    this analysis based on a predefined prompt.
    """

    def __init__(self, config: Config):
        """
        Initializes the AnalyzerAgent with configuration settings.

        Args:
            config (Config): The project-wide configuration object.
        """
        self.config = config
        self.llm = ChatOpenAI(
            api_key=config.OPENAI_API_KEY,
            model=config.LLM_MODEL_NAME,
            temperature=config.LLM_TEMPERATURE,
            max_tokens=config.LLM_MAX_TOKENS,
            top_p=config.LLM_TOP_P
        )
        self.prompt_template = ChatPromptTemplate.from_messages([
            ("system", self.config.PROBLEM_ANALYSIS_PROMPT),
            ("user", "Analyze the following search results:\n\n{search_results}\n\n{format_instructions}")
        ])
        self.parser = JsonOutputParser()

    def _format_search_results(self, search_results: List[Dict[str, Any]]) -> str:
        """
        Formats a list of search results into a clean, readable string for LLM processing.

        Args:
            search_results (List[Dict[str, Any]]): A list of dictionaries, where each
                                                    dictionary represents a search result.
                                                    Expected keys: 'title', 'link', 'snippet'.

        Returns:
            str: A formatted string containing the combined search results.
        """
        formatted_string = []
        for i, result in enumerate(search_results):
            title = clean_text(result.get('title', 'N/A'))
            link = clean_text(result.get('link', 'N/A'))
            snippet = clean_text(result.get('snippet', 'N/A'))

            formatted_string.append(f"--- Result {i+1} ---")
            if title != 'N/A':
                formatted_string.append(f"Title: {title}")
            if link != 'N/A':
                formatted_string.append(f"Link: {link}")
            if snippet != 'N/A':
                formatted_string.append(f"Snippet: {snippet}")
            formatted_string.append("") # Add a blank line for separation

        return "\n".join(formatted_string).strip()

    def analyze_problems(self, search_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Analyzes raw search results to identify real-world problems and pain points.

        This method processes the search results using an LLM to extract structured
        information about problems, their impact, and supporting evidence.
        It handles potential chunking of input if the total content exceeds LLM limits.

        Args:
            search_results (List[Dict[str, Any]]): A list of dictionaries, where each
                                                    dictionary represents a search result
                                                    from the SearchAgent.

        Returns:
            List[Dict[str, Any]]: A list of dictionaries, each describing an identified
                                  problem with its summary, impact, and evidence.
                                  Returns an empty list if no problems are identified
                                  or an error occurs.
        """
        if not search_results:
            logger.warning("No search results provided for analysis.")
            return []

        formatted_results = self._format_search_results(search_results)
        
        # Estimate token count for the formatted results and the prompt structure
        # A simple char-based estimation; actual LLM tokenization is more complex
        max_chunk_chars = self.config.LLM_MAX_TOKENS * 3 # Roughly 3-4 chars per token
        
        # Ensure that the entire prompt (system + user with format instructions)
        # and some buffer space is accounted for.
        # This is a simplification; a true token counter would be better.
        prompt_overhead_chars = len(self.config.PROBLEM_ANALYSIS_PROMPT) + \
                                len("Analyze the following search results:\n\n\n\n") + \
                                len(self.parser.get_format_instructions()) + 500 # buffer
        
        effective_max_content_chars = max_chunk_chars - prompt_overhead_chars
        
        if effective_max_content_chars <= 0:
            logger.error(f"Effective max content chars is {effective_max_content_chars}. "
                         f"LLM_MAX_TOKENS or prompt overhead might be too large.")
            return []

        problem_chunks: List[Dict[str, Any]] = []
        
        # Chunk the results if they are too long for a single LLM call
        result_chunks = chunk_text(formatted_results, max_tokens=effective_max_content_chars, overlap=self.config.LLM_CHUNK_OVERLAP)
        
        logger.info(f"Analyzing {len(result_chunks)} chunk(s) of search results.")

        for i, chunk in enumerate(result_chunks):
            try:
                chain = self.prompt_template | self.llm | self.parser
                response_dict = chain.invoke({
                    "search_results": chunk,
                    "format_instructions": self.parser.get_format_instructions()
                })
                
                # The expected output is a JSON object with a list of problems.
                # Let's ensure the structure is as expected and extract the list.
                if isinstance(response_dict, dict) and "problems" in response_dict and isinstance(response_dict["problems"], list):
                    for problem in response_dict["problems"]:
                        # Validate minimum evidence length if configured
                        evidence = problem.get("evidence", "")
                        if len(evidence) >= self.config.MIN_PROBLEM_EVIDENCE_LENGTH:
                            problem_chunks.append(problem)
                        else:
                            logger.warning(f"Problem skipped due to insufficient evidence length "
                                           f"(length: {len(evidence)}, required: {self.config.MIN_PROBLEM_EVIDENCE_LENGTH}): "
                                           f"{problem.get('summary', 'No summary')}")
                else:
                    logger.warning(f"Chunk {i+1}: LLM response structure unexpected. Expected {{'problems': [...]}}, got {response_dict}. Skipping.")

            except OutputParserException as e:
                logger.error(f"Chunk {i+1}: Failed to parse LLM response: {e}. Raw response might be in an invalid JSON format.")
                logger.debug(f"Invalid JSON response details: {e.args[0]}")
            except Exception as e:
                logger.error(f"Chunk {i+1}: An unexpected error occurred during analysis: {e}")
        
        if not problem_chunks:
            logger.info("No problems identified after analyzing all chunks.")

        return problem_chunks

@register_agent("analyze")
class AnalyzeStep(BaseAgent):
    """
    Pipeline step: turns context['search_results'] into context['problems'] (each assigned a
    stable 'id' for downstream linking to solutions/monetization).
    """
    required_packages = ["langchain_openai", "langchain_core"]

    def __init__(self, config: Config):
        super().__init__(config)
        self.impl = AnalyzerAgent(config)

    def run(self, context: Dict[str, Any]) -> Dict[str, Any]:
        problems = self.impl.analyze_problems(context.get("search_results", []))
        for problem in problems:
            problem.setdefault("id", str(uuid.uuid4()))
        context["problems"] = problems
        return context


if __name__ == "__main__":
    # This block is for testing the AnalyzerAgent in isolation.
    # In a real run, main.py orchestrates the agents.
    
    # Configure basic logging for better feedback during testing
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    # 1. Initialize Config (mock or actual env variables)
    class MockConfig(Config):
        OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "YOUR_OPENAI_API_KEY") # Replace or set env var
        SERPER_API_KEY = os.getenv("SERPER_API_KEY", "YOUR_SERPER_API_KEY") # Not used directly by Analyzer, but good for completeness
        LLM_MODEL_NAME = "gpt-3.5-turbo" # Use a cheaper model for testing
        LLM_TEMPERATURE = 0.5
        LLM_MAX_TOKENS = 1024 # Keep smaller for local testing
        SEARCH_RESULTS_LIMIT = 5
        MIN_PROBLEM_EVIDENCE_LENGTH = 50 # Example minimum length
        LLM_CHUNK_OVERLAP = 100 # Example overlap for chunking

        PROBLEM_ANALYSIS_PROMPT = (
            "You are an expert at identifying real-world problems from text. "
            "Given search results, identify common pain points, challenges, or unmet needs. "
            "For each, provide a 'summary' (concise description), 'impact' (who is affected and how), "
            "and 'evidence' (a quote or paraphrased detail from the text supporting the problem). "
            "Focus on problems that could be solved by a SaaS product. "
            "Return a JSON object with a key 'problems' containing a list of these problem objects."
        )

    mock_config = MockConfig()
    
    if not mock_config.OPENAI_API_KEY or mock_config.OPENAI_API_KEY == "YOUR_OPENAI_API_KEY":
        logger.error("OPENAI_API_KEY not set. Please set the environment variable or replace 'YOUR_OPENAI_API_KEY' in MockConfig.")
    else:
        # 2. Prepare mock search results
        mock_search_results = [
            {
                "title": "Small Businesses Struggle with Inventory Management",
                "link": "https://example.com/small-business-inventory",
                "snippet": "Many small retail businesses report significant issues keeping track of their inventory, leading to lost sales, overstocking, and wasted capital. Manual tracking is time-consuming and error-prone. A survey showed 40% of small businesses cited inventory as their biggest operational headache."
            },
            {
                "title": "Remote Teams Face Communication Breakdowns",
                "link": "https://example.com/remote-team-comm",
                "snippet": "Despite numerous tools, remote teams often struggle with asynchronous communication, leading to misunderstandings, delayed decisions, and a feeling of isolation among employees. 'We spend half our day just trying to get on the same page,' one manager commented."
            },
            {
                "title": "Freelancers Overwhelmed by Client Invoicing and Payments",
                "link": "https://example.com/freelancer-invoicing",
                "snippet": "Independent contractors frequently cite invoicing clients and tracking payments as a major drain on their productivity. Chasing late payments and managing multiple billing cycles manually takes away from billable work. One freelancer said, 'I lose hours every week just dealing with admin, not creative work.'"
            },
            {
                "title": "The rise of AI in customer service",
                "link": "https://example.com/ai-customer-service",
                "snippet": "AI is transforming how companies handle customer inquiries, offering faster responses and 24/7 availability. This development mostly focuses on improving existing solutions rather than highlighting a major problem for businesses. This is more of an opportunity than a problem."
            },
        ]
        
        # 3. Instantiate and run the AnalyzerAgent
        logger.info("Initializing AnalyzerAgent...")
        analyzer = AnalyzerAgent(mock_config)
        logger.info("Analyzing mock search results...")
        identified_problems = analyzer.analyze_problems(mock_search_results)
        
        # 4. Print results
        logger.info("\n--- Identified Problems ---")
        if identified_problems:
            for i, problem in enumerate(identified_problems):
                print(f"Problem {i+1}:")
                print(f"  Summary: {problem.get('summary', 'N/A')}")
                print(f"  Impact: {problem.get('impact', 'N/A')}")
                print(f"  Evidence: {problem.get('evidence', 'N/A')}\n")
        else:
            print("No problems identified.")
        
        # Test with empty results
        logger.info("\n--- Testing with empty search results ---")
        empty_problems = analyzer.analyze_problems([])
        print(f"Identified problems from empty list: {empty_problems}")

        # Test with results that might be too short for evidence
        mock_short_evidence_results = [
            {
                "title": "Short problem",
                "link": "https://example.com/short",
                "snippet": "This problem has very little evidence."
            }
        ]
        # Temporarily reduce min evidence length to allow testing
        original_min_evidence = mock_config.MIN_PROBLEM_EVIDENCE_LENGTH
        mock_config.MIN_PROBLEM_EVIDENCE_LENGTH = 10 
        logger.info(f"\n--- Testing with short evidence (min_evidence={mock_config.MIN_PROBLEM_EVIDENCE_LENGTH}) ---")
        short_evidence_problems = analyzer.analyze_problems(mock_short_evidence_results)
        print(f"Identified problems from short evidence test: {short_evidence_problems}")
        mock_config.MIN_PROBLEM_EVIDENCE_LENGTH = original_min_evidence # Reset

        # Test with a very long result to check chunking (conceptually, won't run full LLM)
        long_snippet = "This is a very long text that simulates a large search result snippet. " * 500
        mock_long_results = [
            {
                "title": "Very Long Problem Description",
                "link": "https://example.com/long-problem",
                "snippet": long_snippet
            }
        ]
        logger.info("\n--- Testing with very long results for chunking ---")
        # For actual LLM calls with long chunks, you'd need a real OpenAI key and a model
        # that can handle the tokens. This example primarily tests the chunking logic.
        print(f"Attempting to analyze long results (will likely involve chunking if real LLM was called).")
        # Skipping actual LLM call for this very long input in __main__ to avoid high costs,
        # but the chunking logic itself can be visually verified by inspecting 'result_chunks' length.
        # For a full test, one would need to mock the LLM or use a real API key.
        # identified_long_problems = analyzer.analyze_problems(mock_long_results) 
        # print(f"Identified problems from long text: {identified_long_problems}")