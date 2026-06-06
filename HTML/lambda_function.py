
import base64
import json
import logging
import os
import uuid
from datetime import datetime, timezone

import boto3
import urllib.request
import urllib.error

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ── AWS clients (initialised once per cold start) ──────────────────────────────
s3       = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")

# ── Config from env vars ───────────────────────────────────────────────────────
HF_API_TOKEN   = os.environ["HF_API_TOKEN"]
S3_BUCKET      = os.environ["S3_BUCKET_NAME"]
DYNAMO_TABLE   = os.environ["DYNAMODB_TABLE"]
HF_BASE_URL    = os.environ.get("HF_API_BASE_URL", "https://api-inference.huggingface.co/models")

TABLE = dynamodb.Table(DYNAMO_TABLE)

# ── CORS headers returned on every response ────────────────────────────────────
CORS = {
    "Access-Control-Allow-Origin":  "*",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "POST,OPTIONS",
}


def response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {**CORS, "Content-Type": "application/json"},
        "body": json.dumps(body),
    }


# ── Main handler ───────────────────────────────────────────────────────────────
def lambda_handler(event, context):
    logger.info("Received event: %s", json.dumps({k: v for k, v in event.items() if k != "body"}))

    # ── Handle CORS pre-flight ────────────────────────────────────────────────
    if event.get("requestContext", {}).get("http", {}).get("method") == "OPTIONS":
        return response(200, {"message": "ok"})

    # ── Parse body ────────────────────────────────────────────────────────────
    try:
        body = json.loads(event.get("body", "{}"))
    except json.JSONDecodeError as exc:
        logger.error("JSON decode error: %s", exc)
        return response(400, {"message": "Invalid JSON body"})

    image_b64    = body.get("image")
    filename     = body.get("filename", "upload.jpg")
    model_id     = body.get("model_id", "google/vit-base-patch16-224")
    content_type = body.get("content_type", "image/jpeg")

    if not image_b64:
        return response(400, {"message": "Missing required field: image (base64)"})

    # ── Decode image ──────────────────────────────────────────────────────────
    try:
        image_bytes = base64.b64decode(image_b64)
    except Exception as exc:
        logger.error("Base64 decode failed: %s", exc)
        return response(400, {"message": "Could not decode base64 image"})

    # ── Upload to S3 ──────────────────────────────────────────────────────────
    item_id  = str(uuid.uuid4())
    s3_key   = f"uploads/{item_id}/{filename}"

    try:
        s3.put_object(
            Bucket=S3_BUCKET,
            Key=s3_key,
            Body=image_bytes,
            ContentType=content_type,
        )
        logger.info("Uploaded image to s3://%s/%s", S3_BUCKET, s3_key)
    except Exception as exc:
        logger.error("S3 upload failed: %s", exc)
        return response(500, {"message": f"S3 upload failed: {str(exc)}"})

    # ── Call HuggingFace Inference API ────────────────────────────────────────
    hf_url = f"{HF_BASE_URL}/{model_id}"
    try:
        predictions = call_huggingface(hf_url, image_bytes, content_type)
    except urllib.error.HTTPError as exc:
        logger.error("HuggingFace HTTP error %s: %s", exc.code, exc.read())
        return response(502, {"message": f"HuggingFace API error: HTTP {exc.code}"})
    except Exception as exc:
        logger.error("HuggingFace call failed: %s", exc)
        return response(502, {"message": f"HuggingFace API call failed: {str(exc)}"})

    # ── Store in DynamoDB ─────────────────────────────────────────────────────
    timestamp = datetime.now(timezone.utc).isoformat()
    db_item = {
        "id":          item_id,
        "timestamp":   timestamp,
        "filename":    filename,
        "model_id":    model_id,
        "s3_bucket":   S3_BUCKET,
        "s3_key":      s3_key,
        "predictions": predictions,          # list of {label, score}
        "top_label":   predictions[0]["label"] if predictions else "unknown",
        "top_score":   str(predictions[0]["score"]) if predictions else "0",
    }

    try:
        TABLE.put_item(Item=db_item)
        logger.info("Stored result in DynamoDB, id=%s", item_id)
    except Exception as exc:
        logger.error("DynamoDB put_item failed: %s", exc)
        # Non-fatal: we still return predictions to the user
        logger.warning("Continuing despite DynamoDB failure")

    # ── Return result to frontend ─────────────────────────────────────────────
    return response(200, {
        "predictions":  predictions,
        "s3_key":       s3_key,
        "db_item_id":   item_id,
        "model_id":     model_id,
        "filename":     filename,
        "timestamp":    timestamp,
    })


# ── HuggingFace helper ─────────────────────────────────────────────────────────
def call_huggingface(url: str, image_bytes: bytes, content_type: str) -> list:
    """
    Send image bytes directly to the HuggingFace Inference API.
    Returns a list of dicts: [{label: str, score: float}, ...]
    """
    req = urllib.request.Request(
        url,
        data=image_bytes,
        headers={
            "Authorization": f"Bearer {HF_API_TOKEN}",
            "Content-Type":  content_type,
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()

    parsed = json.loads(raw)

    # HF returns either a list directly or wraps it
    if isinstance(parsed, list) and parsed and isinstance(parsed[0], list):
        parsed = parsed[0]

    # Normalise to [{label, score}] and sort descending
    results = [
        {"label": item.get("label", "unknown"), "score": round(float(item.get("score", 0)), 6)}
        for item in parsed
        if isinstance(item, dict)
    ]
    results.sort(key=lambda x: x["score"], reverse=True)
    return results
