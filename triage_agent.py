"""
BIT Updates v2 - Triage Agent
Parses incoming complaints and extracts structured entities.
Identifies: category, affected entities, keywords, location, severity signals.
"""

from backend.agents.base_agent import BaseAgent


TRIAGE_PROMPT = """
You are a smart Triage Agent for BIT Wardha campus complaint system.
Analyze the following student complaint and extract structured information.

Complaint Title: {title}
Complaint Description: {description}
Department Hint: {department}

Extract and return a JSON object with EXACTLY these fields:
{{
  "category": "<one of: academic|infrastructure|hostel|library|sports|canteen|transport|it_support|administrative|exam|faculty|other>",
  "sub_category": "<specific sub-category within the category>",
  "entities": {{
    "locations": ["<list of specific locations mentioned>"],
    "people": ["<names of people mentioned>"],
    "dates": ["<dates or time references>"],
    "equipment": ["<equipment or systems mentioned>"],
    "courses": ["<course names or codes mentioned>"]
  }},
  "keywords": ["<top 5 keywords>"],
  "severity_signals": ["<phrases indicating urgency>"],
  "affected_count_estimate": <estimated number of students affected, integer 1-1000>,
  "issue_type": "<one of: complaint|query|request|feedback|emergency>",
  "needs_escalation": <true|false>,
  "escalation_reason": "<reason if needs_escalation is true, else null>",
  "summary": "<one sentence professional summary of the complaint>"
}}

Return ONLY the JSON object, no other text.
"""


class TriageAgent(BaseAgent):
    """
    Triage Agent: Parse & Extract Entities
    First agent in the pipeline. Runs on every new complaint.
    """

    def __init__(self):
        super().__init__(
            name="TriageAgent",
            description="Parses complaints and extracts structured entities using NLP",
        )

    async def process(self, input_data: dict) -> dict:
        title = input_data.get("title", "")
        description = input_data.get("description", "")
        department = input_data.get("department", "Unknown")

        prompt = TRIAGE_PROMPT.format(
            title=title,
            description=description,
            department=department,
        )

        raw_response = await self.gemini_generate(prompt)
        parsed = self.parse_json_response(raw_response)

        if not parsed:
            # Fallback defaults
            parsed = {
                "category": "other",
                "sub_category": "general",
                "entities": {"locations": [], "people": [], "dates": [], "equipment": [], "courses": []},
                "keywords": [],
                "severity_signals": [],
                "affected_count_estimate": 1,
                "issue_type": "complaint",
                "needs_escalation": False,
                "escalation_reason": None,
                "summary": title,
            }

        return {
            "triage": parsed,
            "ai_category": parsed.get("category", "other"),
            "ai_summary": parsed.get("summary", title),
            "needs_escalation": parsed.get("needs_escalation", False),
            "escalation_reason": parsed.get("escalation_reason"),
            "raw_response": raw_response[:500],  # Truncate for storage
        }
