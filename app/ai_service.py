import json
import os

from dotenv import load_dotenv
from openai import OpenAI

from app.ai_schema import ACTION_EXTRACTION_SCHEMA

load_dotenv()


OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")


client = OpenAI(
    api_key=OPENAI_API_KEY,
    base_url=OPENAI_BASE_URL,
    default_headers={
        "HTTP-Referer": "http://127.0.0.1:8000",
        "X-Title": "Personal Action Assistant",
    },
)


SYSTEM_PROMPT = """
Ты — ИИ-операция extract_actions_from_text.

Твоя задача: преобразовать рабочий текст в структурированный JSON.
Нужно извлечь задачи, отделы/подразделения, ответственных, сроки, приоритеты, неясности и определить,
нужна ли ручная проверка.

Правила:
- Не придумывай ответственного, если он не указан.
- Если в тексте указан отдел или подразделение, обязательно заполни поле department.
- Не путай department и assignee: department — это отдел/подразделение, assignee — конкретный ответственный сотрудник.
- Если срок относительный: завтра, до пятницы, к пятнице, на следующей неделе, до конца месяца — поставь needs_review=true.
- Если срок не указан — deadline=null и needs_review=true.
- Если данных недостаточно, ставь needs_review=true.
- Если есть неоднозначность, укажи её в unclear_items.
- Если в тексте указана конкретная дата, возвращай deadline в формате ДД.ММ.ГГГГ, например 30.05.2026.
- Верни только валидный JSON без markdown, без пояснений и без текста до или после JSON.
- Ответ должен строго соответствовать JSON Schema.
"""


def mock_extract_actions_from_text(source_text: str) -> dict:
    text = source_text.lower()

    if "до пятницы" in text or "к пятнице" in text:
        return {
            "summary": "В тексте указана задача, но срок сформулирован неоднозначно.",
            "actions": [
                {
                    "title": "Подготовить КП для клиента Альфа",
                    "description": "Подготовить коммерческое предложение для клиента Альфа.",
                    "assignee": "Марина Петрова",
                    "department": "Коммерческий отдел",
                    "deadline": None,
                    "priority": "medium",
                    "status": "needs_clarification",
                    "source_fragment": source_text,
                }
            ],
            "unclear_items": [
                {
                    "issue": "Неоднозначный срок",
                    "reason": "Фраза 'до пятницы' является относительным сроком и требует ручного уточнения конкретной даты.",
                    "related_fragment": "до пятницы",
                }
            ],
            "overall_priority": "medium",
            "needs_review": True,
            "review_reason": "У задачи указан относительный срок, его нужно подтвердить вручную.",
            "confidence": 0.85,
        }

    if "договорной отдел" in text and "финансовый отдел" in text:
        return {
            "summary": "В тексте зафиксированы задачи для коммерческого, договорного и финансового отделов.",
            "actions": [
                {
                    "title": "Подготовить КП для клиента Альфа",
                    "description": "Подготовить коммерческое предложение для клиента Альфа.",
                    "assignee": "Марина Петрова",
                    "department": "Коммерческий отдел",
                    "deadline": "30.05.2026",
                    "priority": "medium",
                    "status": "new",
                    "source_fragment": "Коммерческий отдел: Марина Петрова, подготовь КП для клиента Альфа до 30.05.2026.",
                },
                {
                    "title": "Проверить договор с поставщиком СтройСнаб",
                    "description": "Проверить договор с поставщиком СтройСнаб.",
                    "assignee": "Игорь Соколов",
                    "department": "Договорной отдел",
                    "deadline": "31.05.2026",
                    "priority": "medium",
                    "status": "new",
                    "source_fragment": "Договорной отдел: Игорь Соколов, проверь договор с поставщиком СтройСнаб до 31.05.2026.",
                },
                {
                    "title": "Подготовить расчет стоимости проекта Бета",
                    "description": "Подготовить расчет стоимости проекта Бета.",
                    "assignee": "Анна Кузнецова",
                    "department": "Финансовый отдел",
                    "deadline": "29.05.2026",
                    "priority": "medium",
                    "status": "new",
                    "source_fragment": "Финансовый отдел: Анна Кузнецова, подготовь расчет стоимости проекта Бета до 29.05.2026.",
                },
            ],
            "unclear_items": [],
            "overall_priority": "medium",
            "needs_review": False,
            "review_reason": None,
            "confidence": 0.95,
        }

    return {
        "summary": "Коммерческий отдел должен подготовить коммерческое предложение для клиента Альфа.",
        "actions": [
            {
                "title": "Подготовить коммерческое предложение для клиента Альфа",
                "description": "Подготовить коммерческое предложение для клиента Альфа.",
                "assignee": "Марина Петрова",
                "department": "Коммерческий отдел",
                "deadline": "30.05.2026",
                "priority": "medium",
                "status": "new",
                "source_fragment": source_text,
            }
        ],
        "unclear_items": [],
        "overall_priority": "medium",
        "needs_review": False,
        "review_reason": None,
        "confidence": 0.95,
    }

def extract_actions_from_text(source_text: str) -> dict:
    """
    Извлекает задачи из рабочего текста через OpenRouter/OpenAI-compatible API.
    Возвращает dict, который дальше сохраняется в базе и валидируется приложением.
    """

    if os.getenv("USE_MOCK_AI", "false").lower() == "true":
        return mock_extract_actions_from_text(source_text)

    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not configured")

    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        temperature=0.2,
        messages=[
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": source_text,
            },
        ],
        response_format={"type": "json_object"},
    )

    content = response.choices[0].message.content

    if not content:
        raise RuntimeError("AI response is empty")

    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"AI response is not valid JSON: {content}") from exc