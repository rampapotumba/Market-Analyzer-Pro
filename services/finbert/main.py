"""
FinBERT Microservice — Financial Sentiment Analysis.

POST /score   → single text scoring
POST /batch   → batch scoring (up to 50 texts)
GET  /health  → health check
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from transformers import AutoModelForSequenceClassification, AutoTokenizer, pipeline

logger = logging.getLogger(__name__)

# Global model reference
_pipeline = None


class ScoreRequest(BaseModel):
    text: str = Field(..., max_length=1024)


class ScoreResponse(BaseModel):
    score: float  # [-1, +1]: positive=+1, negative=-1, neutral=0
    label: str  # "positive", "negative", "neutral"
    confidence: float  # 0-1


class BatchRequest(BaseModel):
    texts: list[str] = Field(..., max_length=50)


class BatchResponse(BaseModel):
    scores: list[ScoreResponse]


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _pipeline
    logger.info("Loading FinBERT model (ProsusAI/finbert)...")
    model_name = "ProsusAI/finbert"
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = AutoModelForSequenceClassification.from_pretrained(model_name)
    _pipeline = pipeline(
        "sentiment-analysis",
        model=model,
        tokenizer=tokenizer,
        device=-1,  # CPU
    )
    logger.info("FinBERT model loaded successfully.")
    yield
    logger.info("Shutting down FinBERT service.")


app = FastAPI(title="FinBERT Service", version="1.0.0", lifespan=lifespan)


def _score_text(text: str) -> ScoreResponse:
    """Score a single text using FinBERT."""
    result = _pipeline(text[:512])[0]  # BERT 512 token limit
    label = result["label"]
    confidence = result["score"]

    if label == "positive":
        score = confidence
    elif label == "negative":
        score = -confidence
    else:
        score = 0.0

    return ScoreResponse(score=round(score, 4), label=label, confidence=round(confidence, 4))


@app.post("/score", response_model=ScoreResponse)
async def score_text(request: ScoreRequest):
    """Score a single financial text for sentiment."""
    if _pipeline is None:
        raise HTTPException(status_code=503, detail="Model not loaded yet")
    return _score_text(request.text)


@app.post("/batch", response_model=BatchResponse)
async def score_batch(request: BatchRequest):
    """Score multiple financial texts for sentiment (max 50)."""
    if _pipeline is None:
        raise HTTPException(status_code=503, detail="Model not loaded yet")
    scores = [_score_text(text) for text in request.texts]
    return BatchResponse(scores=scores)


@app.get("/health")
async def health():
    """Health check."""
    return {
        "status": "ok" if _pipeline is not None else "loading",
        "model": "ProsusAI/finbert",
    }
