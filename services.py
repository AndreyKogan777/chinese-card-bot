import base64
import requests
from config import PERPLEXITY_API_KEY
from prompts.recognition_prompt import RECOGNITION_PROMPT
from prompts.analysis_prompt import ANALYSIS_PROMPT

PERPLEXITY_URL = "https://api.perplexity.ai/chat/completions"


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


def analyze_card_data(card_data: str) -> str:
    prompt_text = ANALYSIS_PROMPT.replace("{card_data}", card_data)
    payload = {
        "model": "sonar-pro",
        "messages": [
            {
                "role": "user",
                "content": prompt_text,
            }
        ],
        "max_tokens": 5000,
        "temperature": 0.0,
        "top_p": 0.1,
    }
    return _call_perplexity(payload)


def analyze_business_card(image_bytes: bytes) -> str:
    card_data = recognize_business_card(image_bytes)
    return analyze_card_data(card_data)
