"""
Pinecone vector memory — stores and retrieves memories by semantic similarity.
Uses Gemini embeddings (768-dim) to encode facts before upserting.
Index: captain-memory  |  Cloud: AWS us-east-1
"""
import hashlib
import json
import time
from typing import Optional

from integrations.manager import get_credential

SERVICE    = "pinecone"
EMBED_DIM  = 768   # must match the Pinecone index dimension
EMBED_MODEL = "gemini-embedding-001"
NAMESPACE  = "jarvis-memory"


def _pc():
    from pinecone import Pinecone
    key = get_credential(SERVICE, "api_key")
    if not key:
        raise RuntimeError("Pinecone API key not configured.")
    return Pinecone(api_key=key)


def _index():
    pc         = _pc()
    index_name = get_credential(SERVICE, "index_name") or "captain-memory"
    return pc.Index(index_name)


def _embed(text: str) -> list[float]:
    """Embed text using Gemini text-embedding-004 via the new google.genai SDK."""
    from pathlib import Path as _Path
    cfg = _Path(__file__).resolve().parent.parent / "config" / "api_keys.json"
    try:
        with open(cfg) as f:
            api_key = json.load(f).get("gemini_api_key", "")
    except Exception:
        api_key = ""

    if not api_key:
        return [0.0] * EMBED_DIM

    try:
        from google import genai as _genai
        client = _genai.Client(api_key=api_key)
        result = client.models.embed_content(
            model=EMBED_MODEL,
            contents=text,
        )
        vec = list(result.embeddings[0].values)
        # Truncate to index dimension (gemini-embedding-001 returns 3072, index is 768)
        if len(vec) > EMBED_DIM:
            vec = vec[:EMBED_DIM]
        elif len(vec) < EMBED_DIM:
            vec = vec + [0.0] * (EMBED_DIM - len(vec))
        return vec
    except Exception as e:
        print(f"[Pinecone] embed error: {e}")
        return [0.0] * EMBED_DIM


def _id(text: str) -> str:
    return "mem-" + hashlib.sha256(text.encode()).hexdigest()[:16]


def store(category: str, key: str, value: str, persona: str = "tommy") -> bool:
    """Upsert a memory fact into Pinecone."""
    try:
        text = f"{category}: {key} = {value}"
        vec  = _embed(text)
        _index().upsert(
            vectors=[{
                "id":     _id(text),
                "values": vec,
                "metadata": {
                    "category": category,
                    "key":      key,
                    "value":    value,
                    "persona":  persona,
                    "ts":       time.time(),
                    "text":     text,
                },
            }],
            namespace=NAMESPACE,
        )
        return True
    except Exception as e:
        print(f"[Pinecone] store error: {e}")
        return False


def search(query: str, top_k: int = 5, persona: str = None) -> list[dict]:
    """Semantic search over stored memories."""
    try:
        vec    = _embed(query)
        filter_ = {"persona": persona} if persona else None
        kwargs  = {"vector": vec, "top_k": top_k, "namespace": NAMESPACE,
                   "include_metadata": True}
        if filter_:
            kwargs["filter"] = filter_
        results = _index().query(**kwargs)
        return [
            {
                "score":    m.score,
                "category": m.metadata.get("category", ""),
                "key":      m.metadata.get("key", ""),
                "value":    m.metadata.get("value", ""),
                "text":     m.metadata.get("text", ""),
            }
            for m in results.matches
            if m.score > 0.6
        ]
    except Exception as e:
        print(f"[Pinecone] search error: {e}")
        return []


def delete(category: str, key: str) -> bool:
    try:
        text = f"{category}: {key}"
        _index().delete(ids=[_id(text)], namespace=NAMESPACE)
        return True
    except Exception as e:
        print(f"[Pinecone] delete error: {e}")
        return False


def is_ready() -> bool:
    try:
        key = get_credential(SERVICE, "api_key")
        return bool(key)
    except Exception:
        return False
