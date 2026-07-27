import os

def _clean(value: str) -> str:
    return value.strip().strip('"').strip("'") if value else value

BOT_TOKEN = _clean(os.environ.get("BOT_TOKEN", ""))
PERPLEXITY_API_KEY = _clean(os.environ.get("PERPLEXITY_API_KEY", ""))
GPL_API_KEY = _clean(os.environ.get("GPL_API_KEY", ""))
GPL_ACCOUNT_NAME = _clean(os.environ.get("GPL_ACCOUNT_NAME", ""))
ADMIN_PASSWORD = _clean(os.environ.get("ADMIN_PASSWORD", ""))
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")
SERPER_API_KEY = os.environ.get("SERPER_API_KEY")
