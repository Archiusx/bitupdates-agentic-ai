"""
BIT Updates v2 - Sentiment Agent
Analyzes emotional tone, urgency, computes priority score, and determines SLA hours.
Combines high-accuracy Gemini parsing with a deterministic offline heuristic fallback.
"""

import json
from enum import Enum
from typing import Optional, Tuple
from base_agent import BaseAgent


# ==============================================================================
# 1. structure and enum definitions (Merged from Jessica's models.py)
# ==============================================================================
class SentimentLabel(str, Enum):
    positive = "positive"
    negative = "negative"
    neutral = "neutral"


class EmotionLabel(str, Enum):
    anger = "anger"
    frustration = "frustration"
    sadness = "sadness"
    confusion = "confusion"
    fear = "fear"
    neutral = "neutral"
    happiness = "happiness"


class PriorityLevel(str, Enum):
    P0 = "P0"  # Critical -> Maps to p1_critical in Piyush's structure
    P1 = "P1"  # High     -> Maps to p2_high
    P2 = "P2"  # Medium   -> Maps to p3_medium
    P3 = "P3"  # Low      -> Maps to p4_low


# ==============================================================================
# 2. upgraded system prompt (Merged from Jessica's prompts.py & Piyush's schema requirements)
# ==============================================================================
SENTIMENT_PROMPT = """
You are a Sentiment and Urgency Analysis Agent in a campus support system.
Your role:
- Analyze student complaint details (Title, Description, Category, Summary).
- Classify overall sentiment as one of: "positive", "negative", "neutral".
- Classify primary emotion as one of: "anger", "frustration", "sadness", "confusion", "fear", "neutral", "happiness".
- Estimate an urgency score from 0 to 100 (integer).
- Assign a priority level based on urgency:
  - 0-24   → "P3"
  - 25-49  → "P2"
  - 50-74  → "P1"
  - 75-100 → "P0"
- Provide a brief reasoning explaining your decision.

Student Issue Context:
Title: {title}
Description: {description}
Category: {category}
Triage Summary: {summary}

You MUST respond with ONLY a single JSON object and NOTHING else.
No prose, no markdown formatting (DO NOT wrap in ```json ... ```), just the pure raw JSON object.

Expected Output JSON Schema:
{{
  "sentiment": "positive | negative | neutral",
  "emotion": "anger | frustration | sadness | confusion | fear | neutral | happiness",
  "urgency_score": 0-100,
  "priority": "P0 | P1 | P2 | P3",
  "reasoning": "short natural language explanation"
}}
"""


# ==============================================================================
# 3. core intelligent agent implementation
# ==============================================================================
class SentimentAgent(BaseAgent):
    """
    Sentiment Agent: Urgency, Emotion, & Priority Scoring.
    Produces unified priority markers used to sort the admin dashboard.
    """

    def __init__(self):
        super().__init__(
            name="SentimentAgent",
            description="Analyzes sentiment, emotion, and computes urgency/priority metrics.",
        )

    @staticmethod
    def _offline_labels(text: str) -> Tuple[SentimentLabel, EmotionLabel, int, PriorityLevel, str]:
        """Offline fallback using deterministic local label heuristics (Merged from your agent.py)"""
        lower = text.lower()
        sentiment = SentimentLabel.neutral
        emotion = EmotionLabel.neutral
        urgency_score = 40
        priority = PriorityLevel.P2
        reasoning = "Offline mode fallback used deterministic local label heuristics."

        if any(word in lower for word in ["great", "thanks", "love", "awesome", "happy"]):
            sentiment = SentimentLabel.positive
            emotion = EmotionLabel.happiness
            urgency_score = 15
            priority = PriorityLevel.P3
            reasoning = "Positive wording detected in complaint text."
        elif any(word in lower for word in ["terrible", "worst", "angry", "furious", "cannot access", "locked", "hacked", "fraud", "failed"]):
            sentiment = SentimentLabel.negative
            urgency_score = 80
            priority = PriorityLevel.P1
            if any(word in lower for word in ["angry", "furious", "terrible", "worst"]):
                emotion = EmotionLabel.anger
            elif any(word in lower for word in ["hacked", "fraud", "security"]):
                emotion = EmotionLabel.fear
                urgency_score = 95
                priority = PriorityLevel.P0
            else:
                emotion = EmotionLabel.frustration
            reasoning = "Negative issue language detected by offline heuristic classifier."
        elif any(word in lower for word in ["confusing", "not sure", "unclear"]):
            sentiment = SentimentLabel.neutral
            emotion = EmotionLabel.confusion
            urgency_score = 35
            priority = PriorityLevel.P2
            reasoning = "Confusion-oriented wording detected by offline heuristic classifier."

        return sentiment, emotion, urgency_score, priority, reasoning

    async def process(self, input_data: dict) -> dict:
        """
        Main interface method for Piyush's Agent Orchestrator.
        """
        title = input_data.get("title", "")
        description = input_data.get("description", "")
        category = input_data.get("ai_category", "other")
        summary = input_data.get("ai_summary", "")
        duplicate_count = input_data.get("duplicate_count", 0)

        # Reconstruct the combined text layout for the fallback heuristic processor
        combined_text = f"Title: {title}. Description: {description}. Category: {category}."

        # 1. Trigger Gemini via Piyush's BaseAgent client
        prompt = SENTIMENT_PROMPT.format(
            title=title,
            description=description,
            category=category,
            summary=summary
        )

        parsed = None
        try:
            raw = await self.gemini_generate(prompt)
            # Use utility method to peel json code blocks if generated
            parsed = self.parse_json_response(raw)
        except Exception:
            # Fallback seamlessly if API drops or fails
            pass

        # 2. Extract results or fallback to deterministic scoring
        if parsed and isinstance(parsed, dict):
            try:
                sentiment_val = parsed.get("sentiment", "neutral")
                emotion_val = parsed.get("emotion", "neutral")
                urgency_score = int(parsed.get("urgency_score", 40))
                priority_str = parsed.get("priority", "P2")
                reasoning = parsed.get("reasoning", "Successfully extracted by model.")
                
                # Normalize schema parameters
                sentiment = SentimentLabel(sentiment_val)
                emotion = EmotionLabel(emotion_val)
                priority = PriorityLevel(priority_str)
            except Exception:
                # Secondary schema-mismatch fallback
                sentiment, emotion, urgency_score, priority, reasoning = self._offline_labels(combined_text)
                reasoning += " Fallback activated due to upstream response structural anomaly."
        else:
            # Primary model/parse failure fallback
            sentiment, emotion, urgency_score, priority, reasoning = self._offline_labels(combined_text)
            reasoning += " Fallback activated due to API downtime or parsing exception."

        # 3. Apply your high-volume Duplicate Boost Rule (>20 duplicates increases severity by 1)
        if duplicate_count > 20:
            reasoning += f" [High Duplicate Volume Detected (count={duplicate_count}): Priority Boosted]"
            if priority == PriorityLevel.P3:
                priority = PriorityLevel.P2
            elif priority == PriorityLevel.P2:
                priority = PriorityLevel.P1
            elif priority == PriorityLevel.P1:
                priority = PriorityLevel.P0

        # 4. Map internal priorities to the UI routing tags expected by Piyush's dashboard & SLAs
        # P0 -> p1_critical (12h), P1 -> p2_high (24h), P2 -> p3_medium (48h), P3 -> p4_low (72h)
        mapping_table = {
            PriorityLevel.P0: ("p1_critical", 90, 12),
            PriorityLevel.P1: ("p2_high", 70, 24),
            PriorityLevel.P2: ("p3_medium", 45, 48),
            PriorityLevel.P3: ("p4_low", 15, 72)
        }
        
        piyush_priority_str, default_score_fallback, sla_hours = mapping_table.get(priority, ("p3_medium", 45, 48))
        
        # Override structural scores based on finalized priority limits
        if priority == PriorityLevel.P0 and urgency_score < 75: urgency_score = 90
        elif priority == PriorityLevel.P1 and not (50 <= urgency_score <= 74): urgency_score = 65
        elif priority == PriorityLevel.P2 and not (25 <= urgency_score <= 49): urgency_score = 35
        elif priority == PriorityLevel.P3 and urgency_score > 24: urgency_score = 15

        # Return payload directly mapping to core expectations
        return {
            "sentiment": {
                "sentiment": sentiment.value,
                "emotion": emotion.value,
                "urgency_score": urgency_score,
                "priority": priority.value,
                "reasoning": reasoning,
                "frustration_level": "high" if emotion in [EmotionLabel.anger, EmotionLabel.frustration] else "medium" if emotion == EmotionLabel.confusion else "none",
                "recommended_sla_hours": sla_hours
            },
            "ai_sentiment": sentiment.value,
            "urgency_score": float(urgency_score / 100.0), # Float converter wrapper matching system expectation boundaries
            "ai_priority": piyush_priority_str,
            "priority_score": urgency_score,
            "recommended_sla_hours": sla_hours,
        }
