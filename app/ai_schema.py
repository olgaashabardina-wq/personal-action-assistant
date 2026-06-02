ACTION_EXTRACTION_SCHEMA = {
    "type": "object",
    "required": [
        "summary",
        "actions",
        "unclear_items",
        "overall_priority",
        "needs_review",
        "review_reason",
        "confidence",
    ],
    "properties": {
        "summary": {
            "type": "string"
        },
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "title",
                    "description",
                    "assignee",
                    "department",
                    "deadline",
                    "priority",
                    "status",
                    "source_fragment",
                ],
                "properties": {
                   "title": {"type": "string"},
                   "description": {"type": "string"},
                   "assignee": {"type": ["string", "null"]},
                   "department": {
                       "type": ["string", "null"],
                       "description": (
                            "Отдел или подразделение, к которому относится задача. "
                            "Например: Коммерческий отдел, Договорной отдел, "
                            "Финансовый отдел, Бухгалтерия, Юридический отдел."
                        ),
                    },
                    "deadline": {"type": ["string", "null"]},
                    "priority": {
                        "type": "string",
                        "enum": ["low", "medium", "high", "critical"],
                    },                                    
                    "status": {
                        "type": "string",
                        "enum": ["new", "needs_clarification"],
                    },
                    "source_fragment": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
        "unclear_items": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["issue", "reason", "related_fragment"],
                "properties": {
                    "issue": {"type": "string"},
                    "reason": {"type": "string"},
                    "related_fragment": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
        "overall_priority": {
            "type": "string",
            "enum": ["low", "medium", "high", "critical"],
        },
        "needs_review": {"type": "boolean"},
        "review_reason": {"type": ["string", "null"]},
        "confidence": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
        },
    },
    "additionalProperties": False,
}