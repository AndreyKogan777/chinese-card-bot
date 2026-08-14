import base64
import json
import logging
import re

import requests

from config import PERPLEXITY_API_KEY
from prompts.recognition_prompt import RECOGNITION_PROMPT
from prompts.identify_prompt import IDENTIFY_PROMPT
from prompts.analysis_prompt import ANALYSIS_PROMPT

PERPLEXITY_URL = "https://api.perplexity.ai/chat/completions"

logger = logging.getLogger(__name__)


def _call_perplexity(payload: dict) -> str:
    headers = {
        "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
        "Content-Type": "application/json",
    }
    resp = requests.post(PERPLEXITY_URL, headers=headers, json=payload, timeout=180)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def recognize_business_card(image_bytes: bytes) -> str:
    b64_image = base64.b64encode(image_bytes).decode("utf-8")
    payload = {
        "model": "sonar-pro",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": RECOGNITION_PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/jpeg;base64," + b64_image},
                    },
                ],
            }
        ],
        "max_tokens": 1500,
        "temperature": 0.0,
    }
    return _call_perplexity(payload)


def identify_company(card_data: str) -> str:
    """
    Второй шаг: точная идентификация китайского юрлица.
    Возвращает JSON-строку с кандидатами, USCC, законным представителем и т.д.
    Строгий JSON-контракт (см. prompts/identify_prompt.py).
    """
    prompt_text = IDENTIFY_PROMPT.replace("{card_data}", card_data)
    payload = {
        "model": "sonar-pro",
        "messages": [
            {"role": "user", "content": prompt_text},
        ],
        "max_tokens": 2500,
        "temperature": 0.0,
        "top_p": 0.1,
    }
    raw = _call_perplexity(payload)
    return _normalize_identification_json(raw)


def _normalize_identification_json(raw: str) -> str:
    """
    Модель иногда оборачивает JSON в ```json ... ``` или добавляет пояснения.
    Достаём валидный JSON, а если не получилось — возвращаем сырой текст,
    чтобы аналитик всё равно мог его прочитать (промпт устойчив к обоим форматам).
    """
    if not raw:
        return "{}"
    # Убираем markdown-обёртку
    stripped = raw.strip()
    stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
    stripped = re.sub(r"\s*```$", "", stripped)
    # Пытаемся найти первый '{' и последний '}' — так вырезается чистый JSON
    first = stripped.find("{")
    last = stripped.rfind("}")
    if first != -1 and last != -1 and last > first:
        candidate = stripped[first:last + 1]
        try:
            parsed = json.loads(candidate)
            return json.dumps(parsed, ensure_ascii=False, indent=2)
        except json.JSONDecodeError:
            logger.warning("identify_company: JSON parse failed, returning raw text")
    return stripped


def analyze_card_data(card_data: str, identification_json: str) -> str:
    prompt_text = (
        ANALYSIS_PROMPT
        .replace("{card_data}", card_data)
        .replace("{identification}", identification_json)
    )
    payload = {
        "model": "sonar-pro",
        "messages": [
            {"role": "user", "content": prompt_text},
        ],
        "max_tokens": 8000,
        "temperature": 0.0,
        "top_p": 0.1,
    }
    return _call_perplexity(payload)


def analyze_business_card(image_bytes: bytes) -> str:
    """
    Полный пайплайн:
    1) распознавание визитки (vision)
    2) идентификация юрлица (JSON: USCC, chinese_name, кандидаты, 法定代表人)
    3) финальный due-diligence отчёт поверх установленной идентификации
    """
    card_data = recognize_business_card(image_bytes)
    logger.info("card recognized, running identification")
    identification_json = identify_company(card_data)
    logger.info("company identified, running analysis")
    return analyze_card_data(card_data, identification_json)
