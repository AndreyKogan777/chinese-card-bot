import asyncio
import base64
import json
import logging
import re
from typing import Optional

import aiohttp
import requests

from config import PERPLEXITY_API_KEY
from prompts.recognition_prompt import RECOGNITION_PROMPT
from prompts.identify_prompt import IDENTIFY_PROMPT
from prompts.analysis_prompt import ANALYSIS_PROMPT
from prompts.synthesis_prompt import SYNTHESIS_PROMPT
from prompts.probe_prompts import (
    LEGAL_REGISTRY_PROMPT,
    COURT_AND_CREDIT_PROMPT,
    EXPORT_AND_CUSTOMS_PROMPT,
    B2B_MARKETPLACES_PROMPT,
    CONTACT_PERSON_PROMPT,
    WEBSITE_PROMPT,
)

PERPLEXITY_URL = "https://api.perplexity.ai/chat/completions"
PROBE_TIMEOUT = 120  # секунд на один probe
WEBSITE_FETCH_TIMEOUT = 20  # секунд на fetch сайта

logger = logging.getLogger(__name__)


# ============================================================
# СИНХРОННЫЕ ВЫЗОВЫ (recognize + identify — последовательные)
# ============================================================

def _call_perplexity_sync(payload: dict) -> str:
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
    return _call_perplexity_sync(payload)


def identify_company(card_data: str) -> str:
    prompt_text = IDENTIFY_PROMPT.replace("{card_data}", card_data)
    payload = {
        "model": "sonar-pro",
        "messages": [{"role": "user", "content": prompt_text}],
        "max_tokens": 2500,
        "temperature": 0.0,
        "top_p": 0.1,
    }
    raw = _call_perplexity_sync(payload)
    return _normalize_json_output(raw)


def _normalize_json_output(raw: str) -> str:
    """
    Извлекает валидный JSON из ответа модели. Если распарсить не удалось —
    возвращает сырой текст (промпты дальше устойчивы к обоим форматам).
    """
    if not raw:
        return "{}"
    stripped = raw.strip()
    stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
    stripped = re.sub(r"\s*```$", "", stripped)
    first = stripped.find("{")
    last = stripped.rfind("}")
    if first != -1 and last != -1 and last > first:
        candidate = stripped[first:last + 1]
        try:
            parsed = json.loads(candidate)
            return json.dumps(parsed, ensure_ascii=False, indent=2)
        except json.JSONDecodeError:
            logger.warning("JSON parse failed, returning raw text")
    return stripped


# ============================================================
# АСИНХРОННЫЕ PROBES (параллельные)
# ============================================================

async def _call_perplexity_async(session: aiohttp.ClientSession, payload: dict) -> str:
    headers = {
        "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
        "Content-Type": "application/json",
    }
    async with session.post(
        PERPLEXITY_URL,
        headers=headers,
        json=payload,
        timeout=aiohttp.ClientTimeout(total=PROBE_TIMEOUT),
    ) as resp:
        resp.raise_for_status()
        data = await resp.json()
        return data["choices"][0]["message"]["content"]


async def _run_probe(
    session: aiohttp.ClientSession,
    topic: str,
    prompt: str,
    identification_json: str,
    max_tokens: int = 3000,
) -> dict:
    """Один probe: возвращает dict, безопасен к ошибкам."""
    filled = prompt.replace("{identification}", identification_json)
    payload = {
        "model": "sonar-pro",
        "messages": [{"role": "user", "content": filled}],
        "max_tokens": max_tokens,
        "temperature": 0.0,
        "top_p": 0.1,
    }
    try:
        raw = await _call_perplexity_async(session, payload)
        normalized = _normalize_json_output(raw)
        try:
            parsed = json.loads(normalized)
            if "topic" not in parsed:
                parsed["topic"] = topic
            return parsed
        except json.JSONDecodeError:
            logger.warning("probe %s returned non-JSON, wrapping as note", topic)
            return {"topic": topic, "facts": [], "notes": normalized[:800]}
    except asyncio.TimeoutError:
        logger.warning("probe %s timed out", topic)
        return {"topic": topic, "facts": [], "notes": "проверка не завершилась за отведённое время"}
    except Exception as e:
        logger.exception("probe %s failed", topic)
        return {"topic": topic, "facts": [], "notes": f"проверка не выполнена: {type(e).__name__}"}


async def _fetch_website_text(url: str) -> Optional[str]:
    """Забирает текст официального сайта. Возвращает None при неудаче."""
    if not url:
        return None
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=WEBSITE_FETCH_TIMEOUT),
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; ChineseCardBot/1.0)",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
        ) as session:
            async with session.get(url, allow_redirects=True) as resp:
                if resp.status >= 400:
                    return None
                # Обрезаем HTML до 15000 символов, оставляем текстовую часть
                html = await resp.text(errors="ignore")
                # Простое обрезание тегов; отдаём модели, она сама разберёт
                text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
                text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
                text = re.sub(r"<[^>]+>", " ", text)
                text = re.sub(r"\s+", " ", text).strip()
                return text[:15000] if text else None
    except Exception as e:
        logger.warning("website fetch failed for %s: %s", url, type(e).__name__)
        return None


def _extract_website_url(identification_json: str, card_data: str) -> Optional[str]:
    """Достаём URL сайта: сначала из identification, потом из card_data."""
    # Из идентификации — иногда модель кладёт в sources
    try:
        parsed = json.loads(identification_json)
        for cand in parsed.get("candidates", []):
            for src in cand.get("sources", []):
                if isinstance(src, str) and re.match(r"^https?://", src):
                    # исключаем крупные реестры и B2B
                    domain = src.split("/")[2].lower() if "/" in src[8:] else ""
                    if not any(x in domain for x in ["qcc.com", "qichacha", "tianyancha", "gsxt", "alibaba", "made-in-china", "1688", "baidu"]):
                        return src
    except Exception:
        pass
    # Из card_data — ищем строку "Сайт: ..." или голый URL
    m = re.search(r"(?:Сайт|Website|网站)[^\S\n]*[:：]?\s*(https?://\S+|www\.\S+|[a-z0-9-]+\.[a-z]{2,}(?:/\S*)?)", card_data, re.IGNORECASE)
    if m:
        url = m.group(1).strip().rstrip(".,;:)")
        return url
    return None


async def _run_all_probes(identification_json: str, card_data: str) -> str:
    """
    Запускает параллельно 5 probe-ов + fetch сайта.
    Возвращает JSON-строку — массив собранных дайджестов по темам.
    """
    website_url = _extract_website_url(identification_json, card_data)
    logger.info("website url for probes: %s", website_url)

    async with aiohttp.ClientSession() as session:
        probe_tasks = [
            _run_probe(session, "legal_registry", LEGAL_REGISTRY_PROMPT, identification_json),
            _run_probe(session, "court_and_credit", COURT_AND_CREDIT_PROMPT, identification_json),
            _run_probe(session, "export_and_customs", EXPORT_AND_CUSTOMS_PROMPT, identification_json),
            _run_probe(session, "b2b_and_marketplaces", B2B_MARKETPLACES_PROMPT, identification_json),
            _run_probe(session, "contact_person", CONTACT_PERSON_PROMPT, identification_json),
        ]
        website_task = _fetch_website_text(website_url) if website_url else None
        if website_task:
            website_html, *probe_results = await asyncio.gather(website_task, *probe_tasks)
        else:
            probe_results = await asyncio.gather(*probe_tasks)
            website_html = None

        # Website probe — отдельно, потому что prompt другой
        if website_html:
            website_prompt_filled = WEBSITE_PROMPT.replace(
                "{identification}", identification_json
            ).replace(
                "{website_content}", f"URL: {website_url}\n\n{website_html}"
            )
            payload = {
                "model": "sonar-pro",
                "messages": [{"role": "user", "content": website_prompt_filled}],
                "max_tokens": 2500,
                "temperature": 0.0,
                "top_p": 0.1,
            }
            try:
                raw = await _call_perplexity_async(session, payload)
                normalized = _normalize_json_output(raw)
                website_result = json.loads(normalized)
                if "topic" not in website_result:
                    website_result["topic"] = "company_website"
                probe_results = list(probe_results) + [website_result]
            except Exception as e:
                logger.warning("website probe failed: %s", type(e).__name__)
                probe_results = list(probe_results) + [{
                    "topic": "company_website",
                    "website_url": website_url,
                    "facts": [],
                    "notes": f"сайт открылся, но синтез из его содержимого не удался: {type(e).__name__}",
                }]
        elif website_url:
            probe_results = list(probe_results) + [{
                "topic": "company_website",
                "website_url": website_url,
                "facts": [],
                "notes": "сайт указан на визитке, но не открылся или пустой",
            }]

    return json.dumps(list(probe_results), ensure_ascii=False, indent=2)


def gather_probes(identification_json: str, card_data: str) -> str:
    """Sync-обёртка над асинхронным сбором probes."""
    return asyncio.run(_run_all_probes(identification_json, card_data))


# ============================================================
# ФИНАЛЬНЫЙ СИНТЕЗ
# ============================================================

def synthesize_report(card_data: str, identification_json: str, probes_digest: str) -> str:
    prompt_text = (
        SYNTHESIS_PROMPT
        .replace("{card_data}", card_data)
        .replace("{identification}", identification_json)
        .replace("{probes_digest}", probes_digest)
    )
    payload = {
        "model": "sonar-pro",
        "messages": [{"role": "user", "content": prompt_text}],
        "max_tokens": 8000,
        "temperature": 0.0,
        "top_p": 0.1,
    }
    return _call_perplexity_sync(payload)


# ============================================================
# ПОЛНЫЙ ПАЙПЛАЙН
# ============================================================

def analyze_business_card(image_bytes: bytes) -> str:
    """
    Этап 2 пайплайна:
    1) recognize (vision)
    2) identify (JSON: USCC, chinese_name, кандидаты)
    3) 5 параллельных probes + fetch сайта → дайджест фактов
    4) synthesize — финальный отчёт по дайджесту (без веб-поиска)
    """
    card_data = recognize_business_card(image_bytes)
    logger.info("stage 1/4: card recognized")
    identification_json = identify_company(card_data)
    logger.info("stage 2/4: company identified")
    probes_digest = gather_probes(identification_json, card_data)
    logger.info("stage 3/4: probes gathered")
    report = synthesize_report(card_data, identification_json, probes_digest)
    logger.info("stage 4/4: report synthesized")
    return report
