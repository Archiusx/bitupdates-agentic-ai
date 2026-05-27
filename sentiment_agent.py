"""
BIT Updates v2 - Sentiment Agent
Analyzes emotional tone, urgency, and computes priority score.
Uses Gemini to understand nuanced student distress signals.
"""

from base_agent import BaseAgent


SENTIMENT_PROMPT = """
You are a Sentiment & Priority Scoring Agent for a campus support system.
Analyze the emotional tone and urgency of this student complaint.

Title: {title}
Description: {description}
Category: {category}
Triage Summary: {summary}

Return a JSON object with EXACTLY these fields:
{{
  "sentiment": "<one of: very_negative|negative|neutral|positive|very_positive>",
  "sentiment_score": <float between -1.0 (very negative) and 1.0 (very positive)>,
  "urgency_level": "<one of: critical|high|medium|low>",
  "urgency_score": <float between 0.0 (not urgent) and 1.0 (extremely urgent)>,
  "priority": "<one of: p1_critical|p2_high|p3_medium|p4_low>",
  "priority_score": <integer 1-100, higher is more urgent>,
  "emotional_indicators": ["<list of phrases showing emotional state>"],
  "frustration_level": "<one of: extreme|high|medium|low|none>",
  "tone_analysis": {{
    "is_formal": <true|false>,
    "is_threatening": <false>,
    "is_desperate": <true|false>,
    "has_previous_attempts": <true|false>,
    "mentions_impact_on_grades": <true|false>
  }},
  "recommended_sla_hours": <integer - recommended response SLA in hours>,
  "priority_justification": "<one sentence explaining priority level>"
}}

Priority scoring guide:
- P1 Critical (score 75-100): Safety, health, exam issues, system outages
- P2 High (score 50-74): Academic deadlines, multiple students affected
- P3 Medium (score 25-49): Infrastructure, general complaints
- P4 Low (score 1-24): Feedback, suggestions, minor issues

Return ONLY the JSON object.
"""


class SentimentAgent(BaseAgent):
    """
    Sentiment Agent: Urgency & Priority Scoring
    Produces a priority score used to sort admin dashboard.
    """

    def __init__(self):
        super().__init__(
            name="SentimentAgent",
            description="Analyzes sentiment and computes urgency/priority scores",
        )

    async def process(self, input_data: dict) -> dict:
        prompt = SENTIMENT_PROMPT.format(
            title=input_data.get("title", ""),
            description=input_data.get("description", ""),
            category=input_data.get("ai_category", "other"),
            summary=input_data.get("ai_summary", ""),
        )

        raw = await self.gemini_generate(prompt)
        parsed = self.parse_json_response(raw)

        if not parsed:
            parsed = {
                "sentiment": "neutral",
                "sentiment_score": 0.0,
                "urgency_level": "medium",
                "urgency_score": 0.5,
                "priority": "p3_medium",
                "priority_score": 40,
                "emotional_indicators": [],
                "frustration_level": "none",
                "tone_analysis": {},
                "recommended_sla_hours": 48,
                "priority_justification": "Default medium priority",
            }

        return {
            "sentiment": parsed,
            "ai_sentiment": parsed.get("sentiment", "neutral"),
            "urgency_score": parsed.get("urgency_score", 0.5),
            "ai_priority": parsed.get("priority", "p3_medium"),
            "priority_score": parsed.get("priority_score", 40),
            "recommended_sla_hours": parsed.get("recommended_sla_hours", 48),
        }
