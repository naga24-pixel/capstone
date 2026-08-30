"""
Phase 4 — RAG Banking Support Agent (OpenAI + FAISS vector DB)
==================================================================
Same purpose as the earlier draft, upgraded to:
  1. Ingest RAW RBI/DICGC PDFs directly (no manual markdown conversion
     needed) alongside the existing curated .md summaries.
  2. Store embeddings in a real vector database (FAISS) instead of a
     flat JSON + numpy cosine-similarity scan — this is what most
     capstone rubrics mean by "use a vector store".

Corpus (data/)
--------------
  *.md          — curated summaries (DICGC guide/FAQ, RBI ombudsman FAQ,
                   condensed KYC direction)
  pdfs/*.pdf    — original RBI source documents:
                   - Master_Circular_RBI.pdf (Customer Service in Banks, 2015)
                   - MD18KYCF...pdf (full KYC Master Direction, 2016, upd. 2025)
                   - RBIOS2021_amendments05082022.pdf (full RB-IOS 2021 legal text)
                   - RBIOS01072026.pdf (full RB-IOS 2026 FAQ)

Note: the .md files and their PDF counterparts (KYC, Ombudsman FAQ)
overlap in content on purpose — the .md gives a fast, clean summary
chunk; the PDF gives the full legal text with clause numbers for
precise citation. Retrieval may surface either or both.

Pipeline
--------
1. Load every .md and .pdf file under data/
2. Chunk: .md by '## ' headers (semantic), .pdf by paragraph with a
   fixed-size + overlap fallback (no reliable header structure in raw PDF text)
3. Embed all chunks with OpenAI embeddings (only for chunks not
   already in the FAISS index — content-hash based incremental indexing)
4. Store vectors in a FAISS IndexFlatIP (cosine similarity via
   normalized vectors) with a parallel metadata store (chunk text +
   source + page), persisted to disk
5. At query time: embed the question, FAISS top-k search, filter by
   a minimum similarity floor
6. Stuff retrieved chunks into the prompt; the LLM must answer using
   ONLY that context and cite sources, or say "I don't know" and
   escalate if retrieval found nothing relevant

Setup
-----
    pip install openai faiss-cpu pypdf numpy

    export OPENAI_API_KEY="sk-..."      (macOS/Linux)
    setx OPENAI_API_KEY "sk-..."        (Windows)

Run
---
    python agent_v3_rag.py
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path

import faiss
import numpy as np
from dotenv import load_dotenv
from openai import OpenAI
from pypdf import PdfReader

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
CHAT_MODEL = "gpt-4o-mini"
EMBED_MODEL = "text-embedding-3-small"   # 1536-dim
EMBED_DIM = 1536

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR.parent / ".env")
DATA_DIR = BASE_DIR / "data"
PDF_DIR = DATA_DIR / "pdfs"
INDEX_DIR = BASE_DIR / "vector_store"
FAISS_INDEX_PATH = INDEX_DIR / "index.faiss"
METADATA_PATH = INDEX_DIR / "metadata.json"
LOG_PATH = BASE_DIR.parent / "logs" / "phase4_runs.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
INDEX_DIR.mkdir(parents=True, exist_ok=True)

TOP_K = 4
MIN_SIMILARITY = 0.20
CHUNK_MAX_CHARS = 1200
CHUNK_OVERLAP_CHARS = 150

client = OpenAI()  # reads OPENAI_API_KEY from environment

SYSTEM_PROMPT = """\
You are an AI Banking Support and Advisory Agent for a retail bank, \
grounded in Indian banking regulations (RBI / DICGC).

Rules you must always follow:
1. Answer ONLY using the CONTEXT provided below, which is retrieved from \
official RBI/DICGC documents. Do not use outside knowledge for policy \
specifics (limits, timelines, coverage amounts, procedures).
2. If the CONTEXT does not contain the answer, say exactly: \
"I don't know based on the available information," and note that the \
query is being escalated to a human representative. Do not guess.
3. Never execute, approve, or advise on money transfers, payments, \
withdrawals, credit-limit increases, loan approvals, or other financial \
transactions. Politely refuse these and direct the customer to the \
official banking app or a human representative.
4. Escalate ambiguous, suspicious, high-risk, account-access, or \
identity-related questions instead of guessing.
5. If a message contains multiple questions or requests, address EACH \
one separately and explicitly.
6. Cite the source document name(s) you drew on, in parentheses, at the \
end of your answer, e.g. (Source: dicgc_guide_to_deposit_insurance.md) \
or (Source: RBIOS2021_amendments05082022.pdf).
7. Keep answers concise and professional.
"""


# ---------------------------------------------------------------------------
# 1. Load + chunk documents (Markdown and PDF)
# ---------------------------------------------------------------------------
def load_markdown_chunks() -> list[dict]:
    chunks = []
    for path in sorted(DATA_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        sections = re.split(r"\n(?=## )", text)
        for section in sections:
            section = section.strip()
            if not section:
                continue
            chunks.extend(_hard_wrap(section, source=path.name))
    return chunks


def load_pdf_chunks() -> list[dict]:
    """Extract text per page, split into paragraphs, then hard-wrap.
    Page number is kept in metadata for precise citation.

    Important: the project keeps PDFs both at the top level of data/ and under
    data/pdfs/, so we scan both locations to avoid silently ignoring the RBI
    legal source documents when the index is built.
    """
    chunks = []
    pdf_paths = set()
    if DATA_DIR.exists():
        pdf_paths.update(DATA_DIR.glob("*.pdf"))
    if PDF_DIR.exists():
        pdf_paths.update(PDF_DIR.glob("*.pdf"))

    for path in sorted(pdf_paths, key=lambda p: str(p.name)):
        reader = PdfReader(str(path))
        for page_num, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if not text:
                continue
            paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
            buffer = ""
            for para in paragraphs:
                if len(buffer) + len(para) <= CHUNK_MAX_CHARS:
                    buffer = f"{buffer}\n{para}".strip()
                else:
                    if buffer:
                        chunks.extend(_hard_wrap(buffer, source=path.name, page=page_num))
                    buffer = para
            if buffer:
                chunks.extend(_hard_wrap(buffer, source=path.name, page=page_num))
    return chunks


def _hard_wrap(text: str, source: str, page: int | None = None) -> list[dict]:
    """Split oversized text into CHUNK_MAX_CHARS pieces with overlap."""
    if len(text) <= CHUNK_MAX_CHARS:
        return [{"source": source, "page": page, "text": text}]
    pieces = []
    start = 0
    while start < len(text):
        end = start + CHUNK_MAX_CHARS
        pieces.append({"source": source, "page": page, "text": text[start:end]})
        start = end - CHUNK_OVERLAP_CHARS
    return pieces


def load_all_chunks() -> list[dict]:
    return load_markdown_chunks() + load_pdf_chunks()


# ---------------------------------------------------------------------------
# 2. Embeddings
# ---------------------------------------------------------------------------
def embed_texts(texts: list[str]) -> np.ndarray:
    """Returns an (n, EMBED_DIM) float32 array of L2-normalized embeddings,
    ready for cosine similarity via inner product in FAISS."""
    response = client.embeddings.create(model=EMBED_MODEL, input=texts)
    vectors = np.array([item.embedding for item in response.data], dtype="float32")
    faiss.normalize_L2(vectors)
    return vectors


def _chunk_id(chunk: dict) -> str:
    """Content hash so we can detect exactly which chunks are new/changed
    without re-embedding the whole corpus every run."""
    raw = f"{chunk['source']}|{chunk.get('page')}|{chunk['text']}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 3. Vector store (FAISS) — build or incrementally update
# ---------------------------------------------------------------------------
def build_or_load_index() -> tuple[faiss.Index, list[dict]]:
    current_chunks = load_all_chunks()
    if not current_chunks:
        raise SystemExit(f"No .md or .pdf source files found under {DATA_DIR}.")

    for chunk in current_chunks:
        chunk["id"] = _chunk_id(chunk)

    if FAISS_INDEX_PATH.exists() and METADATA_PATH.exists():
        index = faiss.read_index(str(FAISS_INDEX_PATH))
        metadata: list[dict] = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
        existing_ids = {m["id"] for m in metadata}
    else:
        index = faiss.IndexFlatIP(EMBED_DIM)
        metadata = []
        existing_ids = set()

    new_chunks = [c for c in current_chunks if c["id"] not in existing_ids]

    if new_chunks:
        print(f"Embedding {len(new_chunks)} new/changed chunk(s)...")
        vectors = embed_texts([c["text"] for c in new_chunks])
        index.add(vectors)
        metadata.extend(new_chunks)

        faiss.write_index(index, str(FAISS_INDEX_PATH))
        METADATA_PATH.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")

    return index, metadata


# ---------------------------------------------------------------------------
# 4. Retrieval
# ---------------------------------------------------------------------------
def retrieve(query: str, index: faiss.Index, metadata: list[dict], top_k: int = TOP_K) -> list[dict]:
    query_vector = embed_texts([query])
    scores, indices = index.search(query_vector, top_k)

    results = []
    for score, idx in zip(scores[0], indices[0]):
        if idx == -1:
            continue
        if score >= MIN_SIMILARITY:
            chunk = metadata[idx]
            results.append({**chunk, "score": float(score)})
    return results


# ---------------------------------------------------------------------------
# 5. PII masking + logging
# ---------------------------------------------------------------------------
def mask_pii(text: str) -> str:
    text = re.sub(r"\b\d{6,}\b", "[REDACTED_NUMBER]", text)
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "[REDACTED_EMAIL]", text)
    return text


def log_interaction(query: str, response: str, retrieved: list[dict]) -> None:
    timestamp = datetime.now().isoformat(timespec="seconds")
    if retrieved:
        source_labels = sorted(
            {r["source"] + (f"#p{r['page']}" if r.get("page") else "") for r in retrieved}
        )
        sources = ", ".join(source_labels)
    else:
        sources = "NONE (escalated)"
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(
            f"[{timestamp}] sources={sources}\n"
            f"  Q: {mask_pii(query)}\n"
            f"  A: {mask_pii(response)}\n"
        )


# ---------------------------------------------------------------------------
# 6. Answer generation
# ---------------------------------------------------------------------------
def _format_context_entry(r: dict) -> str:
    label = f"Source: {r['source']}"
    if r.get("page"):
        label += f", page {r['page']}"
    return f"[{label}]\n{r['text']}"


def ask(query: str, index: faiss.Index, metadata: list[dict]) -> str:
    retrieved = retrieve(query, index, metadata)

    if not retrieved:
        answer = (
            "I don't know based on the available information. This query is "
            "being escalated to a human representative."
        )
        log_interaction(query, answer, retrieved)
        return answer

    context_block = "\n\n---\n\n".join(_format_context_entry(r) for r in retrieved)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"CONTEXT:\n{context_block}\n\nQUESTION: {query}"},
    ]

    completion = client.chat.completions.create(
        model=CHAT_MODEL,
        messages=messages,
        temperature=0,
    )
    answer = completion.choices[0].message.content
    log_interaction(query, answer, retrieved)
    return answer


# ---------------------------------------------------------------------------
# Demo queries — same set as before, plus one that specifically needs the
# raw legal PDF text (compensation limits differ between RB-IOS 2021 and
# RB-IOS 2026 — a good test that retrieval pulls the RIGHT version).
# ---------------------------------------------------------------------------
DEMO_QUERIES = [
    "Can you increase my credit limit?",
    "Transfer €500 to my friend's account",
    "Is my account frozen? I can't log in.",
    "What is the maximum amount insured by DICGC if my bank fails?",
    "How do I escalate a complaint to the RBI Ombudsman, and is there a fee?",
    "What documents do I need to open a bank account in India?",
    "My deposits are split across 3 branches of the same bank — are they insured separately?",
    "What is the maximum compensation the RBI Ombudsman can award for mental anguish?",
    "What is the routing number for wire transfers to the USA?",  # out-of-corpus, should escalate
]


def run_demo() -> None:
    print("Building/loading FAISS vector index...")
    index, metadata = build_or_load_index()
    n_sources = len({m["source"] for m in metadata})
    print(f"Index has {index.ntotal} vectors from {n_sources} source file(s)")
    print(f"Logging to: {LOG_PATH}\n")

    for query in DEMO_QUERIES:
        answer = ask(query, index, metadata)
        print(f"Q: {query}\nA: {answer}\n")


if __name__ == "__main__":
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit(
            "OPENAI_API_KEY is not set. Run:\n"
            '  export OPENAI_API_KEY="sk-..."   (macOS/Linux)\n'
            '  setx OPENAI_API_KEY "sk-..."     (Windows)\n'
            "then re-run this script."
        )

    idx, meta = build_or_load_index()
    run_demo()

    print("--- Interactive mode (type 'exit' to quit) ---")
    while True:
        user_input = input("Question: ").strip()
        if user_input.lower() in {"exit", "quit"}:
            break
        if not user_input:
            continue
        print(f"A: {ask(user_input, idx, meta)}\n")
