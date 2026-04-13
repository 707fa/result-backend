import json
import os
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request


AI_SYSTEM_PROMPT = """
You are Iman AI, an English learning assistant for school students.
Primary tasks:
1) Help with English grammar, vocabulary, and speaking.
2) If the student sends homework text/photo, review it and provide clear feedback:
   - what is correct,
   - what mistakes are present,
   - corrected version,
   - short tips for improvement.
3) Keep tone supportive and concise.
4) Reply in the same language as the student message when possible.
""".strip()


logger = logging.getLogger(__name__)


def _get_int_env(name, default, min_value=0, max_value=10):
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    if value < min_value:
        return min_value
    if value > max_value:
        return max_value
    return value


def _mock_reply(text, has_image):
    if has_image and text:
        return (
            "AI service is temporarily unavailable. I saved your photo and message.\n"
            "Please try again in 1-2 minutes.\n"
            f"Your message: {text}"
        )
    if has_image:
        return (
            "AI service is temporarily unavailable. I received your photo. "
            "Please send a short text with what to check and try again in 1-2 minutes."
        )
    if text:
        return (
            "AI service is temporarily unavailable. "
            "Please try again in 1-2 minutes.\n"
            f"Your message: {text}"
        )
    return "AI service is temporarily unavailable. Send text or homework photo and try again."


def _extract_data_url_parts(image_data_url):
    if not image_data_url:
        return None, None

    match = re.match(r"^data:(image/[a-zA-Z0-9.+-]+);base64,(.+)$", str(image_data_url).strip())
    if not match:
        return None, None

    return match.group(1), match.group(2)


def _post_json(url, headers, payload, timeout=60):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    # Ignore broken system proxy values (for example 127.0.0.1:9) and send direct HTTPS request.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as response:
        return response.read().decode("utf-8")


def _extract_openai_text(payload):
    if not isinstance(payload, dict):
        return ""

    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    output = payload.get("output")
    if not isinstance(output, list):
        return ""

    parts = []
    for item in output:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") in {"output_text", "text"} and isinstance(block.get("text"), str):
                parts.append(block["text"].strip())

    return "\n".join([part for part in parts if part]).strip()


def _extract_gemini_text(payload):
    if not isinstance(payload, dict):
        return ""

    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        return ""

    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        content = candidate.get("content")
        if not isinstance(content, dict):
            continue
        parts = content.get("parts")
        if not isinstance(parts, list):
            continue

        texts = []
        for part in parts:
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                texts.append(text.strip())

        if texts:
            return "\n".join(texts).strip()

    return ""


def _generate_with_openai(user_text, image_data_url):
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    model = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini").strip() or "gpt-4.1-mini"
    if not api_key:
        return None

    user_content = []
    if user_text:
        user_content.append({"type": "input_text", "text": user_text})
    if image_data_url:
        user_content.append({"type": "input_image", "image_url": image_data_url})
    if not user_content:
        user_content.append({"type": "input_text", "text": "Help me with English homework."})

    payload = {
        "model": model,
        "input": [
            {"role": "system", "content": [{"type": "input_text", "text": AI_SYSTEM_PROMPT}]},
            {"role": "user", "content": user_content},
        ],
    }

    try:
        raw = _post_json(
            "https://api.openai.com/v1/responses",
            {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            payload,
            timeout=60,
        )
        data = json.loads(raw)
        answer = _extract_openai_text(data)
        return answer or None
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        logger.warning("[IMAN_AI][OPENAI] request failed: %s", exc)
        return None


def _generate_with_gemini(user_text, image_data_url):
    api_key = os.environ.get("GEMINI_API_KEY", "").strip() or os.environ.get("GOOGLE_API_KEY", "").strip()
    configured_model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash"
    if not api_key:
        return None

    max_retries = _get_int_env("GEMINI_MAX_RETRIES", 2, min_value=0, max_value=6)
    retry_delay_ms = _get_int_env("GEMINI_RETRY_DELAY_MS", 1200, min_value=200, max_value=10000)

    mime_type, image_base64 = _extract_data_url_parts(image_data_url)

    parts = [
        {
            "text": (
                f"{AI_SYSTEM_PROMPT}\n\n"
                f"Student message:\n{user_text or 'Help me with English homework.'}"
            )
        }
    ]
    if mime_type and image_base64:
        parts.append(
            {
                "inlineData": {
                    "mimeType": mime_type,
                    "data": image_base64,
                }
            }
        )

    payload = {
        "contents": [
            {
                "role": "user",
                "parts": parts,
            }
        ]
    }

    model_candidates = []
    for model_name in [configured_model, "gemini-2.5-flash"]:
        if model_name and model_name not in model_candidates:
            model_candidates.append(model_name)

    api_versions = ["v1beta", "v1"]

    for model in model_candidates:
        for api_version in api_versions:
            endpoint = (
                f"https://generativelanguage.googleapis.com/{api_version}/models/"
                f"{urllib.parse.quote(model)}:generateContent?key={urllib.parse.quote(api_key)}"
            )

            for attempt in range(max_retries + 1):
                try:
                    raw = _post_json(endpoint, {"Content-Type": "application/json"}, payload, timeout=90)
                    data = json.loads(raw)
                    answer = _extract_gemini_text(data)
                    if answer:
                        return answer
                    break
                except urllib.error.HTTPError as exc:
                    body = ""
                    try:
                        body = exc.read().decode("utf-8", "ignore")[:800]
                    except Exception:
                        body = ""
                    logger.warning(
                        "[IMAN_AI][GEMINI] HTTP %s model=%s api=%s attempt=%s/%s error=%s",
                        exc.code,
                        model,
                        api_version,
                        attempt + 1,
                        max_retries + 1,
                        body,
                    )

                    retryable = exc.code in {429, 500, 502, 503, 504}
                    if retryable and attempt < max_retries:
                        time.sleep((retry_delay_ms * (attempt + 1)) / 1000.0)
                        continue
                    break
                except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                    logger.warning(
                        "[IMAN_AI][GEMINI] request failed model=%s api=%s attempt=%s/%s: %s",
                        model,
                        api_version,
                        attempt + 1,
                        max_retries + 1,
                        exc,
                    )
                    if attempt < max_retries:
                        time.sleep((retry_delay_ms * (attempt + 1)) / 1000.0)
                        continue
                    break

    return None


def generate_iman_ai_reply(text="", image_data_url=None):
    user_text = (text or "").strip()
    has_image = bool(image_data_url and str(image_data_url).strip())
    provider = (os.environ.get("AI_PROVIDER", "gemini") or "gemini").strip().lower()

    if provider == "openai":
        reply = _generate_with_openai(user_text, image_data_url)
        if reply:
            return reply
        reply = _generate_with_gemini(user_text, image_data_url)
        if reply:
            return reply
        return _mock_reply(user_text, has_image)

    # default: gemini
    reply = _generate_with_gemini(user_text, image_data_url)
    if reply:
        return reply
    reply = _generate_with_openai(user_text, image_data_url)
    if reply:
        return reply

    return _mock_reply(user_text, has_image)
