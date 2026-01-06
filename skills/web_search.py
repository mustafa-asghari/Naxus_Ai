from ddgs import DDGS
from core.models import ActionStep, Result

def search_web(step: ActionStep) -> Result:
    """
    Performs a web search and returns the top results as a text summary.
    """
    # 1. Extract the search query from the planner's command
    query = (step.args or {}).get("query")
    if not query:
        return Result(ok=False, message="No search query provided.")  

    try:
        results = []
        # 2. Initialize the DuckDuckGo search engine
        with DDGS() as ddgs:
            # 3. Fetch up to 3 results (we don't want to overwhelm the AI with too much text)
            # 'text' gives us the search results
            for result in ddgs.text(query, max_results=3):
                title = result.get('title', '')
                href = result.get('href', '')
                body = result.get('body', '')
                
                # Format each result clearly so the AI can read it easily
                results.append(f"Title: {title}\nURL: {href}\nSnippet: {body}\n---")

        if not results:
            return Result(ok=True, message=f"No results found for '{query}'.")

        # 4. Join all snippets into one big string to send back to the brain
        full_text = "\n".join(results)
        return Result(ok=True, message=f"Search Results for '{query}':\n\n{full_text}")

    except Exception as e:
        return Result(ok=False, message=f"Search failed: {e}")