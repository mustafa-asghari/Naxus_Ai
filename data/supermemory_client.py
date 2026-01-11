"""
Supermemory Integration - Graph-based AI Memory

Uses Supermemory.ai API for intelligent, evolving memory with:
- Entity extraction and relationship tracking
- Semantic search and recall
- Memory evolution as data changes
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from supermemory import Supermemory


_client: Optional[Supermemory] = None


def _get_client() -> Supermemory:
    """Get or create Supermemory client."""
    global _client
    if _client is None:
        api_key = os.getenv("SUPERMEMORY_API_KEY")
        if not api_key:
            raise RuntimeError(
                "SUPERMEMORY_API_KEY not set. "
                "Add it to your .env file: SUPERMEMORY_API_KEY=your_key"
            )
        _client = Supermemory(api_key=api_key)
    return _client


def add_memory(
    content: str,
    user_id: str = "default",
    metadata: Optional[Dict[str, Any]] = None,
    tags: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Add a memory to Supermemory.
    
    Args:
        content: The text content to remember
        user_id: User identifier for personalized memory
        metadata: Optional metadata (title, category, etc.)
        tags: Optional container tags for organization
    
    Returns:
        Response with document ID and status
    """
    client = _get_client()
    
    try:
        # Add document to Supermemory
        response = client.documents.add(
            content=content,
            container_tags=tags or [],
            metadata=metadata or {},
        )
        
        return {
            "ok": True,
            "id": response.id,
            "status": response.status,
            "message": f"Remembered: {content[:50]}..."
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def search_memories(
    query: str,
    user_id: str = "default",
    limit: int = 5,
    tags: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Search memories using semantic search.
    
    Args:
        query: Search query
        user_id: User identifier
        limit: Maximum results to return
        tags: Optional filter by container tags
    
    Returns:
        List of matching memories with scores
    """
    client = _get_client()
    
    try:
        response = client.memories.search(
            query=query,
            top_k=limit,
            container_tags=tags or [],
        )
        
        results = []
        for mem in response.results:
            results.append({
                "content": mem.content if hasattr(mem, 'content') else str(mem),
                "score": mem.score if hasattr(mem, 'score') else 0.0,
                "id": mem.id if hasattr(mem, 'id') else None,
            })
        
        return {
            "ok": True,
            "count": len(results),
            "results": results
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "results": []}


def delete_memory(document_id: str) -> Dict[str, Any]:
    """Delete a specific memory by ID."""
    client = _get_client()
    
    try:
        client.documents.delete(id=document_id)
        return {"ok": True, "deleted_id": document_id}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def list_memories(
    user_id: str = "default",
    limit: int = 20,
    tags: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """List all memories for a user."""
    client = _get_client()
    
    try:
        response = client.documents.list(
            limit=limit,
            container_tags=tags or [],
        )
        
        docs = []
        for doc in response.results:
            docs.append({
                "id": doc.id,
                "content": doc.content[:100] + "..." if len(doc.content) > 100 else doc.content,
                "created_at": str(doc.created_at) if hasattr(doc, 'created_at') else None,
            })
        
        return {"ok": True, "count": len(docs), "documents": docs}
    except Exception as e:
        return {"ok": False, "error": str(e), "documents": []}


# Convenience aliases
remember = add_memory
recall = search_memories
forget = delete_memory
