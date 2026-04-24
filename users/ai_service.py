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

FOUNDATION_LEVELS = {"beginner", "elementary"}


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


def _get_csv_env(name, default_values):
    raw = os.environ.get(name, "")
    if raw is None:
        raw = ""
    parts = [item.strip() for item in str(raw).split(",")]
    values = [item for item in parts if item]
    if values:
        return values
    return list(default_values)


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


def _normalize_level(level):
    raw = str(level or "").strip().lower()
    if "beginner" in raw:
        return "beginner"
    if "elementary" in raw:
        return "elementary"
    if "pre" in raw and "inter" in raw:
        return "pre-intermediate"
    if "upper" in raw and "inter" in raw:
        return "intermediate"
    if "intermediate" in raw:
        return "intermediate"
    return "beginner"


def _resolve_language(level, language):
    preferred = str(language or "").strip().lower()
    normalized_level = _normalize_level(level)
    if normalized_level in FOUNDATION_LEVELS:
        if preferred in {"ru", "uz", "en"}:
            return preferred
        return "ru"
    return "en"


def _build_context_instruction(level="", language="", group_title="", group_time="", system_context=""):
    normalized_level = _normalize_level(level)
    reply_language = _resolve_language(normalized_level, language)

    if normalized_level in FOUNDATION_LEVELS:
        language_rule = (
            f"Reply language: {reply_language.upper()} for explanations and tips. "
            "Also include short simple English examples."
        )
    else:
        language_rule = "Reply language: English only. Keep it clear and concise."

    lines = [
        f"Student level: {normalized_level}.",
        f"Student group: {group_title or 'Unknown group'}.",
        f"Class time: {group_time or 'Unknown time'}.",
        language_rule,
    ]

    if system_context:
        lines.append(f"Additional teacher context: {system_context}")

    return "\n".join(lines), normalized_level, reply_language


def _word_limit_instruction(max_words):
    try:
        value = int(max_words)
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    return (
        f"Keep the main answer concise: around {value} words (hard max {value + 20}). "
        "Use short paragraphs and avoid repetition."
    )


def _trim_to_word_limit(text, max_words):
    try:
        value = int(max_words)
    except (TypeError, ValueError):
        return text
    if value <= 0:
        return text
    words = str(text or "").split()
    if len(words) <= value:
        return str(text or "").strip()
    return " ".join(words[:value]).strip()


def _generate_with_openai(user_text, image_data_url, system_prompt=AI_SYSTEM_PROMPT):
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
            {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]},
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


def _generate_with_gemini(user_text, image_data_url, system_prompt=AI_SYSTEM_PROMPT):
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
                f"{system_prompt}\n\n"
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

    fallback_models = _get_csv_env(
        "GEMINI_FALLBACK_MODELS",
        ["gemini-2.5-pro", "gemini-2.5-flash-lite"],
    )

    model_candidates = []
    for model_name in [configured_model, "gemini-2.5-flash", *fallback_models]:
        if model_name and model_name not in model_candidates:
            model_candidates.append(model_name)

    api_versions = ["v1", "v1beta"]

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


def generate_iman_ai_reply(
    text="",
    image_data_url=None,
    level="",
    language="",
    group_title="",
    group_time="",
    system_context="",
    provider_order=None,
    max_words=None,
    response_mode="chat",
):
    user_text = (text or "").strip()
    has_image = bool(image_data_url and str(image_data_url).strip())
    context_instruction, _, _ = _build_context_instruction(
        level=level,
        language=language,
        group_title=group_title,
        group_time=group_time,
        system_context=system_context,
    )
    instructions = [AI_SYSTEM_PROMPT, context_instruction]
    if response_mode != "json":
        limit_instruction = _word_limit_instruction(max_words)
        if limit_instruction:
            instructions.append(limit_instruction)
    system_prompt = "\n\n".join(part for part in instructions if part).strip()

    if provider_order is None:
        configured = str(os.environ.get("AI_PROVIDER_ORDER", "") or "").strip().lower()
        if configured:
            provider_order = [item.strip() for item in configured.split(",") if item.strip()]
        else:
            default_provider = (os.environ.get("AI_PROVIDER", "gemini") or "gemini").strip().lower()
            provider_order = [default_provider, "openai" if default_provider == "gemini" else "gemini"]

    normalized_order = []
    for provider in provider_order:
        key = str(provider or "").strip().lower()
        if key in {"gemini", "openai"} and key not in normalized_order:
            normalized_order.append(key)
    if not normalized_order:
        normalized_order = ["gemini", "openai"]

    for provider in normalized_order:
        if provider == "gemini":
            reply = _generate_with_gemini(user_text, image_data_url, system_prompt=system_prompt)
        else:
            reply = _generate_with_openai(user_text, image_data_url, system_prompt=system_prompt)
        if reply:
            if response_mode == "json":
                return reply
            return _trim_to_word_limit(reply, max_words)

    fallback = _mock_reply(user_text, has_image)
    if response_mode == "json":
        return fallback
    return _trim_to_word_limit(fallback, max_words)


def _extract_json_payload(raw_text):
    text = str(raw_text or "").strip()
    if not text:
        return None

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    if fenced:
        snippet = fenced.group(1).strip()
        try:
            parsed = json.loads(snippet)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    first = text.find("{")
    last = text.rfind("}")
    if first >= 0 and last > first:
        snippet = text[first : last + 1]
        try:
            parsed = json.loads(snippet)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    return None


def _safe_score(value, fallback=0):
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        parsed = fallback
    return max(0, min(100, parsed))


def _normalize_mistakes(raw_value):
    if not isinstance(raw_value, list):
        return []

    normalized = []
    for item in raw_value[:20]:
        if not isinstance(item, dict):
            continue
        original = str(item.get("original", "")).strip()
        corrected = str(item.get("corrected", "")).strip()
        reason = str(item.get("reason", "")).strip()
        if not original and not corrected and not reason:
            continue
        normalized.append(
            {
                "original": original,
                "corrected": corrected,
                "reason": reason,
            }
        )
    return normalized


def generate_speaking_analysis(question, transcript, level="", language="", group_title="", group_time=""):
    normalized_level = _normalize_level(level)
    reply_language = _resolve_language(normalized_level, language)

    prompt = "\n".join(
        [
            "You are an English speaking evaluator for students.",
            "Return strict JSON only (no markdown).",
            "JSON schema:",
            "{",
            '  "score": number,',
            '  "grammarScore": number,',
            '  "fluencyScore": number,',
            '  "vocabularyScore": number,',
            '  "transcript": "string",',
            '  "correctedAnswer": "string",',
            '  "mistakes": [{"original":"string","corrected":"string","reason":"string"}],',
            '  "feedback": "string",',
            '  "modelAnswer": "string",',
            '  "levelEstimate": "string"',
            "}",
            "Scoring rules: each score must be 0..100.",
            (
                f"Feedback language rule: {reply_language.upper()} with short simple English examples."
                if normalized_level in FOUNDATION_LEVELS
                else "Feedback language rule: English only."
            ),
            f"Student level: {normalized_level}",
            f"Student group: {group_title or 'Unknown group'}",
            f"Class time: {group_time or 'Unknown time'}",
            f"Question: {question}",
            f"Transcript: {transcript}",
        ]
    )

    raw_reply = generate_iman_ai_reply(
        text=prompt,
        level=normalized_level,
        language=reply_language,
        group_title=group_title,
        group_time=group_time,
        response_mode="json",
    )

    parsed = _extract_json_payload(raw_reply)
    if not parsed:
        return {
            "score": 0,
            "grammarScore": 0,
            "fluencyScore": 0,
            "vocabularyScore": 0,
            "transcript": transcript,
            "correctedAnswer": "",
            "mistakes": [],
            "feedback": str(raw_reply or "").strip() or "AI returned empty feedback.",
            "modelAnswer": "",
            "levelEstimate": normalized_level,
        }

    return {
        "score": _safe_score(parsed.get("score"), 0),
        "grammarScore": _safe_score(parsed.get("grammarScore"), 0),
        "fluencyScore": _safe_score(parsed.get("fluencyScore"), 0),
        "vocabularyScore": _safe_score(parsed.get("vocabularyScore"), 0),
        "transcript": str(parsed.get("transcript") or transcript).strip(),
        "correctedAnswer": str(parsed.get("correctedAnswer") or "").strip(),
        "mistakes": _normalize_mistakes(parsed.get("mistakes")),
        "feedback": str(parsed.get("feedback") or "").strip(),
        "modelAnswer": str(parsed.get("modelAnswer") or "").strip(),
        "levelEstimate": str(parsed.get("levelEstimate") or normalized_level).strip(),
    }
