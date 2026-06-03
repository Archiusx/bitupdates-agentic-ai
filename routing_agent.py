"""
BIT Updates v2 - Routing Agent
Determines the correct department for a complaint with confidence scores.
Considers complaint content, category, and campus department structure.
"""

from base_agent import BaseAgent


# BIT Wardha department structure
DEPARTMENTS = {
    "academic": "Academic Affairs Office",
    "exam": "Examination Cell",
    "library": "Central Library",
    "hostel": "Hostel Warden Office",
    "canteen": "Canteen Committee",
    "sports": "Sports Department",
    "transport": "Transport Office",
    "it": "IT & Network Support",
    "admin": "Administrative Office",
    "finance": "Finance & Accounts",
    "placement": "Training & Placement Cell",
    "infrastructure": "Civil Maintenance",
    "electrical": "Electrical Maintenance",
    "medical": "Medical Center",
    "security": "Campus Security",
    "cse": "Computer Science Department",
    "ece": "Electronics Department",
    "mech": "Mechanical Department",
    "civil": "Civil Engineering Department",
}

ROUTING_PROMPT = """
You are a Routing Agent for BIT Wardha campus support system.
Determine which department(s) should handle this complaint.

Complaint Title: {title}
Description: {description}
Category: {category}
Keywords: {keywords}
Triage Summary: {summary}
User-Selected Department: {user_department}

Available departments:
{departments}

Return a JSON object with EXACTLY these fields:
{{
  "primary_department": "<department key from the list>",
  "primary_department_name": "<full department name>",
  "confidence": <float 0.0-1.0>,
  "secondary_departments": ["<list of department keys that may also be involved>"],
  "routing_reason": "<one sentence explaining routing decision>",
  "requires_multi_department": <true|false>,
  "escalate_to_principal": <true|false>,
  "suggested_assignee_role": "<one of: department_head|admin|warden|principal|faculty|it_support>",
  "routing_path": ["<ordered list of departments in escalation path>"],
  "auto_close_eligible": <true|false>,
  "auto_close_reason": "<reason if auto_close_eligible is true, else null>"
}}

Return ONLY the JSON object.
"""


class RoutingAgent(BaseAgent):
    """
    Routing Agent: Department Assignment
    Smart department routing using Gemini + campus knowledge.
    """

    def __init__(self):
        super().__init__(
            name="RoutingAgent",
            description="Routes complaints to correct departments with confidence scoring",
        )

    async def process(self, input_data: dict) -> dict:
        dept_list = "\n".join([f"- {k}: {v}" for k, v in DEPARTMENTS.items()])

        prompt = ROUTING_PROMPT.format(
            title=input_data.get("title", ""),
            description=input_data.get("description", ""),
            category=input_data.get("ai_category", "other"),
            keywords=", ".join(input_data.get("triage", {}).get("keywords", [])),
            summary=input_data.get("ai_summary", ""),
            user_department=input_data.get("department", "Not specified"),
            departments=dept_list,
        )

        raw = await self.gemini_generate(prompt)
        parsed = self.parse_json_response(raw)

        if not parsed:
            parsed = {
                "primary_department": "admin",
                "primary_department_name": "Administrative Office",
                "confidence": 0.5,
                "secondary_departments": [],
                "routing_reason": "Default routing",
                "requires_multi_department": False,
                "escalate_to_principal": False,
                "suggested_assignee_role": "admin",
                "routing_path": ["admin"],
                "auto_close_eligible": False,
                "auto_close_reason": None,
            }

        return {
            "routing": parsed,
            "ai_suggested_department": parsed.get("primary_department", "admin"),
            "routing_confidence": parsed.get("confidence", 0.5),
            "routing_reason": parsed.get("routing_reason", ""),
            "escalate_to_principal": parsed.get("escalate_to_principal", False),
        }
