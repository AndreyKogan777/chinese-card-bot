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
PROBE_CONCURRENCY = 2  # сколько probe-ов одновременно (Perplexity plan бьёт на конкурентности)
PROBE_MAX_RETRIES = 3  # повторы при 429/5xx с backoffом
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
    """
    Вызов Perplexity API с повторами при 429/5xx.
    Подробно логирует ошибку (статус + начало тела), чтобы в следующий раз
    было видно первопричину (лимит, ключ, timeout).
    """
    headers = {
        "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
        "Content-Type": "application/json",
    }
    last_exc = None
    for attempt in range(1, PROBE_MAX_RETRIES + 1):
        try:
            async with session.post(
                PERPLEXITY_URL,
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=PROBE_TIMEOUT),
            ) as resp:
                if resp.status >= 400:
                    body = (await resp.text())[:500]
                    logger.warning(
                        "Perplexity API %s on attempt %d/%d, body: %s",
                        resp.status, attempt, PROBE_MAX_RETRIES, body,
                    )
                    # 429 и 5xx — повторяем с backoffом; 4xx (кроме 429) — не повторяем
                    if resp.status == 429 or resp.status >= 500:
                        if attempt < PROBE_MAX_RETRIES:
                            backoff = 2 ** attempt + (attempt * 0.5)  # 2.5, 4.5, ...
                            await asyncio.sleep(backoff)
                            continue
                    resp.raise_for_status()
                data = await resp.json()
                return data["choices"][0]["message"]["content"]
        except (aiohttp.ClientResponseError, aiohttp.ClientConnectionError, asyncio.TimeoutError) as e:
            last_exc = e
            if attempt < PROBE_MAX_RETRIES:
                backoff = 2 ** attempt
                logger.warning(
                    "Perplexity transport error %s on attempt %d/%d, retry in %.1fs",
                    type(e).__name__, attempt, PROBE_MAX_RETRIES, backoff,
                )
                await asyncio.sleep(backoff)
                continue
            raise
    # сюда не должны дойти, но на всякий случай
    if last_exc:
        raise last_exc
    raise RuntimeError("Perplexity call failed without exception")


async def _run_probe(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
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
        async with semaphore:
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
    """Забирает текст официального сайта С ВИЗИТКИ. Возвращает None при неудаче.

    НИКОГДА не пытается подменить сайт визитки чем-то найденным в поиске.
    """
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
    """
    Достаём URL официального сайта СТРОГО из визитки.

    Важно: нельзя брать сайт из результатов поиска (candidate.sources) — там
    легко попадается домен другого юрлица с похожим именем (напр.,
    zjzhongtian.com вместо aab.com для визитки с aab.com). Сайт с визитки —
    единственный источник, где сама компания явно указала свой официальный домен.
    """
    # 1) Сначала — явная строка "Сайт:/Website:/网站:" в card_data
    m = re.search(r"(?:Сайт|Website|网站)[^\S\n]*[:：]?\s*(https?://\S+|www\.\S+|[a-z0-9-]+\.[a-z]{2,}(?:/\S*)?)", card_data, re.IGNORECASE)
    if m:
        url = m.group(1).strip().rstrip(".,;:)")
        return url

    # 2) Голый URL в card_data (иногда визитка не содержит явного префикса)
    m = re.search(r"(?<![@\w])(www\.[a-z0-9-]+\.[a-z]{2,}(?:/\S*)?|https?://[a-z0-9-]+\.[a-z]{2,}(?:/\S*)?)", card_data, re.IGNORECASE)
    if m:
        url = m.group(1).strip().rstrip(".,;:)")
        # исключаем очевидные не-сайты
        low = url.lower()
        if not any(x in low for x in ["wechat", "weixin", "qq.com", "linkedin.com", "whatsapp"]):
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
        # Семафор ограничивает конкурентность вызовов Perplexity API,
        # чтобы не нарываться на 429 rate-limit.
        semaphore = asyncio.Semaphore(PROBE_CONCURRENCY)
        probe_tasks = [
            _run_probe(session, semaphore, "legal_registry", LEGAL_REGISTRY_PROMPT, identification_json),
            _run_probe(session, semaphore, "court_and_credit", COURT_AND_CREDIT_PROMPT, identification_json),
            _run_probe(session, semaphore, "export_and_customs", EXPORT_AND_CUSTOMS_PROMPT, identification_json),
            _run_probe(session, semaphore, "b2b_and_marketplaces", B2B_MARKETPLACES_PROMPT, identification_json),
            _run_probe(session, semaphore, "contact_person", CONTACT_PERSON_PROMPT, identification_json),
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
                async with semaphore:
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
    """
    Sync-обёртка над асинхронным сбором probes.

    Важно: эта функция может быть вызвана из контекста, где уже есть работающий
    asyncio event loop (например, если когда-либо будет вызвана напрямую из aiogram-handler-а).
    asyncio.run() в этом случае бросает RuntimeError. Поэтому перед вызовом
    проверяем, есть ли работающий loop:
    - нет — обычный asyncio.run
    - есть — запускаем корутину в отдельном потоке со своим чистым loop’ом.
    """
    try:
        asyncio.get_running_loop()
        # Есть активный loop — запускаем в отдельном потоке
        import threading
        result_container = {}
        error_container = {}

        def runner():
            try:
                result_container["value"] = asyncio.run(
                    _run_all_probes(identification_json, card_data)
                )
            except BaseException as e:
                error_container["error"] = e

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()
        thread.join()
        if "error" in error_container:
            raise error_container["error"]
        return result_container["value"]
    except RuntimeError:
        # Нет активного loop’а — обычный путь
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
