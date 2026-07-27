import base64
import json
import requests
from config import PERPLEXITY_API_KEY, DEEPSEEK_API_KEY, SERPER_API_KEY
from prompts.recognition_prompt import RECOGNITION_PROMPT
from prompts.query_generation_prompt import QUERY_GENERATION_PROMPT
from prompts.writing_prompt import WRITING_PROMPT

PERPLEXITY_URL = "https://api.perplexity.ai/chat/completions"
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
SERPER_URL = "https://google.serper.dev/search"


def _call_perplexity(payload: dict) -> str:
    headers = {
        "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
        "Content-Type": "application/json",
    }
    resp = requests.post(PERPLEXITY_URL, headers=headers, json=payload, timeout=180)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def _call_deepseek(payload: dict) -> str:
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    resp = requests.post(DEEPSEEK_URL, headers=headers, json=payload, timeout=180)
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


def _generate_search_queries(card_data: str) -> list:
    prompt_text = QUERY_GENERATION_PROMPT.replace("{card_data}", card_data)
    payload = {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": prompt_text}],
        "max_tokens": 800,
        "temperature": 0.3,
    }
    raw = _call_deepseek(payload)
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
    try:
        queries = json.loads(raw)
        if isinstance(queries, list):
            return [str(q) for q in queries][:12]
    except (json.JSONDecodeError, ValueError):
        pass
    return [line.strip("-• ").strip() for line in raw.split("\n") if line.strip()][:12]


def _serper_search(query: str) -> str:
    headers = {
        "X-API-KEY": SERPER_API_KEY,
        "Content-Type": "application/json",
    }
    payload = {"q": query, "num": 6}
    try:
        resp = requests.post(SERPER_URL, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException:
        return ""
    results = data.get("organic", [])
    lines = []
    for item in results[:6]:
        title = item.get("title", "")
        snippet = item.get("snippet", "")
        link = item.get("link", "")
        if title or snippet:
            lines.append(f"- {title}\n  {snippet}\n  Источник: {link}")
    return "\n".join(lines)


def _collect_search_results(queries: list) -> str:
    all_results = []
    for query in queries:
        result_block = _serper_search(query)
        if result_block:
            all_results.append(f"Запрос: {query}\n{result_block}")
    return "\n\n".join(all_results) if all_results else "Результаты поиска не найдены."


def write_report(card_data: str, search_results: str) -> str:
    prompt_text = WRITING_PROMPT.replace("{card_data}", card_data).replace(
        "{search_results}", search_results
    )
    payload = {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": prompt_text}],
        "max_tokens": 5000,
        "temperature": 0.2,
    }
    return _call_deepseek(payload)


def analyze_card_data(card_data: str) -> str:
    queries = _generate_search_queries(card_data)
    search_results = _collect_search_results(queries)
    return write_report(card_data, search_results)


def analyze_business_card(image_bytes: bytes) -> str:
    card_data = recognize_business_card(image_bytes)
    return analyze_card_data(card_data)

