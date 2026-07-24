import base64
import requests
from config import PERPLEXITY_API_KEY
from prompts.analysis_prompt import ANALYSIS_PROMPT

PERPLEXITY_URL = "https://api.perplexity.ai/chat/completions"

COMBINED_INSTRUCTION = (
    "На фото визитка китайской компании. Распознай текстовый контент с визитки: "
    "имя, должность, компания, адрес, телефоны, email, сайт, WeChat, ассоциации, сертификаты. "
    "Указывай только те поля, которые реально присутствуют на фото. "
    "Не описывай логотипы, эмблемы или пиктограммы на визитке. "
    "Затем на основе распознанных текстовых данных выполни поиск в интернете, включая "
    "официальный сайт компании, если он указан, и подготовь аналитический отчет по "
    "следующей структуре:\n\n"
    + ANALYSIS_PROMPT.replace("{card_data}", "(см. распознанные выше текстовые данные визитки)")
)


def analyze_business_card(image_bytes: bytes) -> str:
    b64_image = base64.b64encode(image_bytes).decode("utf-8")
    headers = {
        "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "sonar-pro",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": COMBINED_INSTRUCTION},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/jpeg;base64," + b64_image},
                    },
                ],
            }
        ],
        "max_tokens": 5000,
        "temperature": 0.2,
    }
    resp = requests.post(PERPLEXITY_URL, headers=headers, json=payload, timeout=180)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]

