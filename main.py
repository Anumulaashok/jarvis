import logging
from typing import List, Dict, Any

from config import Config
from agent.core import AgentCore
from agents.base import discover_agents, AGENT_REGISTRY
from agents.self_extend import (
    SessionState,
    CORE_REQUIRED_PACKAGES,
    check_missing_packages,
    install_missing_packages,
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

DEFAULT_TARGET_QUERIES: List[str] = [
    "CRM for small and medium businesses",
    "Project management software for remote teams",
    "Customer support platforms with integrated AI chatbots",
    "E-commerce analytics tools for Shopify store owners",
    "HR management systems for startups",
]


def _preflight_dependency_check(config: Config, state: SessionState) -> None:
    """
    Checks every currently-registered agent's required_packages (plus the core set) before the
    run starts. Missing packages are installed via self_extend, which checkpoints `state` first
    and restarts the process if anything had to be installed — so code after this call only
    runs once dependencies are actually satisfied.
    """
    discover_agents()
    required = set(CORE_REQUIRED_PACKAGES)
    for step_cls in AGENT_REGISTRY.values():
        required.update(step_cls.required_packages)

    missing = check_missing_packages(sorted(required))
    if missing:
        install_missing_packages(missing, config, state)  # re-execs the process on success


def main() -> None:
    print("SaaS Problem-Solving Agent - Starting...")

    try:
        config = Config()
        target_queries = config.TARGET_QUERIES or DEFAULT_TARGET_QUERIES

        state = SessionState.load(config)
        if state is not None:
            target_queries = state.target_queries
            if state.pending_gap_actions:
                logger.warning(f"Resuming after an interrupted install/scaffold: {state.pending_gap_actions}")
            else:
                logger.info(f"Resuming checkpointed run {state.run_id} from query "
                            f"{state.current_query_index + 1}/{len(state.target_queries)}.")
        else:
            state = SessionState.new(target_queries)

        _preflight_dependency_check(config, state)  # may re-exec and never return

        agent = AgentCore(config)

        print(f"\nInitiating problem identification and solution generation for "
              f"{len(target_queries)} queries (starting at {state.current_query_index + 1})...")

        for i in range(state.current_query_index, len(target_queries)):
            query = target_queries[i]
            print(f"\n--- Processing Query {i + 1}/{len(target_queries)}: '{query}' ---")
            try:
                result = agent.identify_and_solve(target_saas_domain_or_keywords=query, state=state)

                if result:
                    state.results.append(result)
                    print(f"  SUCCESS: Identified problem and proposed solution for '{query}'.")
                    print(f"    Problem: {result.get('problem_statement', 'N/A')[:100]}...")
                    print(f"    Monetization: {result.get('monetization_strategy', 'N/A')[:100]}...")
                else:
                    print(f"  SKIPPED: No viable problem statement or solution identified for '{query}'.")
            except Exception as inner_e:
                print(f"  ERROR: Failed to process query '{query}' due to: {inner_e}")
                import traceback
                traceback.print_exc()
            finally:
                # Checkpoint after every query (success, skip, or error) so a crash or manual
                # re-run resumes from the next query instead of starting over.
                state.current_query_index = i + 1
                state.save(config)

        all_results: List[Dict[str, Any]] = state.results

        print("\n--- Agent Workflow Summary ---")
        if all_results:
            print(f"Successfully identified and proposed solutions for {len(all_results)} out of "
                  f"{len(target_queries)} queries.")
            for i, res in enumerate(all_results):
                print(f"\nResult {i + 1}:")
                print(f"  Target Area: {res.get('target_area', 'N/A')}")
                print(f"  Identified Problem: {res.get('problem_statement', 'N/A')}")
                print(f"  Proposed Solution: {res.get('solution_summary', 'N/A')}")
                print(f"  Monetization Strategy: {res.get('monetization_strategy', 'N/A')}")
                if 'details' in res and isinstance(res['details'], str):
                    print(f"  Details: {res['details'][:200]}...")
        else:
            print("No problems or solutions were identified during this run across all queries.")

        state.clear(config)  # full pass completed; next run starts a fresh checkpoint

    except ImportError as ie:
        print(f"\nERROR: Failed to import a required module. Please ensure all project files are present and correctly structured: {ie}")
    except Exception as e:
        print(f"\nAn unexpected critical error occurred during agent execution: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("\nSaaS Problem-Solving Agent - Shutting Down.")


if __name__ == "__main__":
    main()
