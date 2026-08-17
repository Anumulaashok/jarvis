import os
import json
import time
import requests
from typing import List, Dict, Any, Optional, Union

from openai import OpenAI

from config import Config
from utils.helpers import clean_text, chunk_text
from agents.base import BaseAgent, register_agent

class SearchAgent:
    """
    Agent responsible for executing web searches to find real-world problems and
    retrieving relevant data using the Serper API.
    """

    def __init__(self, config: Config):
        """
        Initializes the SearchAgent with a configuration object.

        Args:
            config (Config): The project-wide configuration settings.
        """
        self.config = config
        self._validate_config()

    def _validate_config(self) -> None:
        """
        Validates essential configuration settings for the search agent.
        """
        if not self.config.SERPER_API_KEY:
            raise ValueError("SERPER_API_KEY is not set in Config.")
        if self.config.SEARCH_RESULTS_LIMIT <= 0:
            raise ValueError("SEARCH_RESULTS_LIMIT must be a positive integer.")
        if self.config.SEARCH_TIMEOUT_SECONDS <= 0:
            raise ValueError("SEARCH_TIMEOUT_SECONDS must be a positive integer.")

    def _execute_serper_search(self, query: str) -> Optional[Dict[str, Any]]:
        """
        Executes a single search query using the Serper API.

        Args:
            query (str): The search query string.

        Returns:
            Optional[Dict[str, Any]]: A dictionary containing the JSON response from Serper,
                                     or None if the request fails.
        """
        url = "https://google.serper.dev/search"
        headers = {
            'X-API-KEY': self.config.SERPER_API_KEY,
            'Content-Type': 'application/json'
        }
        payload = json.dumps({
            "q": query,
            "num": self.config.SEARCH_RESULTS_LIMIT
        })

        try:
            response = requests.post(url, headers=headers, data=payload,
                                     timeout=self.config.SEARCH_TIMEOUT_SECONDS)
            response.raise_for_status()  # Raise an exception for HTTP errors
            return response.json()
        except requests.exceptions.HTTPError as http_err:
            print(f"HTTP error occurred during Serper search for '{query}': {http_err}")
            print(f"Response content: {response.text}")
        except requests.exceptions.ConnectionError as conn_err:
            print(f"Connection error occurred during Serper search for '{query}': {conn_err}")
        except requests.exceptions.Timeout as timeout_err:
            print(f"Timeout error occurred during Serper search for '{query}': {timeout_err}")
        except requests.exceptions.RequestException as req_err:
            print(f"An unexpected request error occurred during Serper search for '{query}': {req_err}")
        except json.JSONDecodeError as json_err:
            print(f"JSON decode error occurred for Serper response for '{query}': {json_err}")
            print(f"Raw response text: {response.text}")
        return None

    def search_problems(self, topic_or_queries: Union[str, List[str]]) -> List[Dict[str, Any]]:
        """
        Executes web searches based on a topic or a list of predefined queries
        to find real-world problems.

        If a single topic string is provided, it's assumed to be a direct query.
        For query generation capabilities, an LLM would typically be used, but
        this method directly takes queries or uses a single topic as a query.

        Args:
            topic_or_queries (Union[str, List[str]]): A single topic string to search for,
                                                      or a list of specific search queries.

        Returns:
            List[Dict[str, Any]]: A list of dictionaries, where each dictionary represents
                                  a search result and contains 'query', 'title', 'link',
                                  and 'snippet'.
        """
        if isinstance(topic_or_queries, str):
            queries = [topic_or_queries]
        else:
            queries = topic_or_queries

        all_results: List[Dict[str, Any]] = []
        unique_links = set()

        for query in queries:
            print(f"Executing search for query: '{query}'")
            response_data = self._execute_serper_search(query)

            if response_data and response_data.get('organic'):
                for result in response_data['organic']:
                    link = result.get('link')
                    if link and link not in unique_links:
                        processed_result = {
                            "query": query,
                            "title": clean_text(result.get('title', '')),
                            "link": link,
                            "snippet": clean_text(result.get('snippet', ''))
                        }
                        all_results.append(processed_result)
                        unique_links.add(link)
                print(f"Found {len(response_data['organic'])} organic results for query '{query}'. Total unique results: {len(all_results)}")
            else:
                print(f"No organic results found for query: '{query}' or Serper API call failed.")

            # Introduce a small delay to avoid hitting rate limits if multiple queries are made
            time.sleep(0.5)

        return all_results

@register_agent("search")
class SearchStep(BaseAgent):
    """
    Pipeline step: expands context['topic'] into concrete search queries via the LLM, then
    runs them through SearchAgent. Populates context['search_queries'] and
    context['search_results'].
    """
    required_packages = ["requests", "openai"]

    def __init__(self, config: Config):
        super().__init__(config)
        self.impl = SearchAgent(config)
        self._query_llm = OpenAI(api_key=config.OPENAI_API_KEY)

    def _generate_search_queries(self, topic: str) -> List[str]:
        try:
            response = self._query_llm.chat.completions.create(
                model=self.config.LLM_MODEL_NAME,
                messages=[
                    {"role": "system", "content": self.config.SEARCH_QUERY_GENERATION_PROMPT},
                    {"role": "user", "content": f"Topic: {topic}"},
                ],
                temperature=self.config.LLM_TEMPERATURE,
                max_tokens=512,
            )
            raw = response.choices[0].message.content or ""
            queries = [line.strip("-• \t") for line in raw.splitlines() if line.strip()]
            if queries:
                return queries[:5]
        except Exception as e:
            print(f"Warning: Failed to generate search queries via LLM for '{topic}': {e}. "
                  f"Falling back to the raw topic as the sole query.")
        return [topic]

    def run(self, context: Dict[str, Any]) -> Dict[str, Any]:
        topic = context["topic"]
        queries = self._generate_search_queries(topic)
        context["search_queries"] = queries
        context["search_results"] = self.impl.search_problems(queries)
        return context


if __name__ == "__main__":
    # This block is for testing purposes only.
    # In a real scenario, API keys would be set as environment variables.
    # For local testing, you might temporarily set them or use a .env file.
    # os.environ["SERPER_API_KEY"] = "YOUR_SERPER_API_KEY" # Uncomment and set for testing

    try:
        current_config = Config()
        if not current_config.SERPER_API_KEY:
            print("SERPER_API_KEY is not set. Please set it as an environment variable "
                  "or directly in config.py for testing.")
            exit(1)

        search_agent = SearchAgent(current_config)

        test_topic = "challenges in remote work productivity"
        print(f"\nSearching for problems related to: '{test_topic}'")
        results_from_topic = search_agent.search_problems(test_topic)
        print(f"\n--- Results for topic '{test_topic}' ({len(results_from_topic)} unique results) ---")
        for i, result in enumerate(results_from_topic[:3]): # Print first 3 results
            print(f"Result {i+1}:")
            print(f"  Query: {result.get('query')}")
            print(f"  Title: {result.get('title')}")
            print(f"  Link: {result.get('link')}")
            print(f"  Snippet: {result.get('snippet', '')[:150]}...") # Truncate snippet
            print("-" * 20)

        test_queries = [
            "small business cash flow problems",
            "freelancer client acquisition challenges",
            "online education engagement issues"
        ]
        print(f"\nSearching for problems using specific queries: {test_queries}")
        results_from_queries = search_agent.search_problems(test_queries)
        print(f"\n--- Results for specific queries ({len(results_from_queries)} unique results) ---")
        for i, result in enumerate(results_from_queries[:3]): # Print first 3 results
            print(f"Result {i+1}:")
            print(f"  Query: {result.get('query')}")
            print(f"  Title: {result.get('title')}")
            print(f"  Link: {result.get('link')}")
            print(f"  Snippet: {result.get('snippet', '')[:150]}...") # Truncate snippet
            print("-" * 20)

    except ValueError as e:
        print(f"Configuration Error: {e}")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")