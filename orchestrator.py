"""
BIT Updates v2 - Orchestrator Agent
Central coordinator using Gemini 2.0 Flash for intent classification and routing.
Manages the full multi-agent pipeline for complaint processing.
Uses LangGraph for stateful agent orchestration.
"""

import asyncio
from datetime import datetime, timezone
from typing import Any, Optional
from bson import ObjectId
import structlog

from backend.agents.base_agent import BaseAgent
from backend.agents.triage_agent import TriageAgent
from backend.agents.sentiment_agent import SentimentAgent
from backend.agents.routing_agent import RoutingAgent
from backend.agents.chatbot_agent import ChatbotAgent
from backend.agents.analytics_agent import AnalyticsAgent
from backend.agents.notification_agent import NotificationAgent
from backend.utils.database import mongo

log = structlog.get_logger(__name__)

# Intent classification prompt
INTENT_PROMPT = """
Classify the intent of this campus support request.

Request: {request}

Return JSON:
{{
  "intent": "<one of: new_complaint|status_query|chat_query|analytics_request|escalation|faq|announcement>",
  "confidence": <float 0.0-1.0>,
  "requires_agents": ["<list of agents needed: triage|sentiment|routing|chatbot|analytics|notification>"],
  "priority_hint": "<critical|high|medium|low>",
  "reasoning": "<one sentence>"
}}

Return ONLY the JSON object.
"""


class OrchestratorAgent(BaseAgent):
    """
    Orchestrator Agent: Intent Classification & Pipeline Routing
    The brain of the multi-agent system. Coordinates all other agents.
    Implements the full architecture pipeline from the system design.
    """

    def __init__(self):
        super().__init__(
            name="OrchestratorAgent",
            description="Central coordinator for multi-agent complaint processing pipeline",
        )
        # Initialize all agents
        self.triage = TriageAgent()
        self.sentiment = SentimentAgent()
        self.routing = RoutingAgent()
        self.chatbot = ChatbotAgent()
        self.analytics = AnalyticsAgent()
        self.notification = NotificationAgent()

    async def process(self, input_data: dict) -> dict:
        """
        Main orchestration logic.
        Routes to appropriate agent pipeline based on intent.
        """
        intent_data = await self._classify_intent(input_data)
        intent = intent_data.get("intent", "chat_query")
        input_data["intent"] = intent
        input_data["intent_data"] = intent_data

        if intent == "new_complaint":
            return await self._process_new_complaint(input_data)
        elif intent == "chat_query" or intent == "faq":
            return await self._process_chat(input_data)
        elif intent == "analytics_request":
            return await self._process_analytics(input_data)
        elif intent == "status_query":
            return await self._process_status_query(input_data)
        else:
            return await self._process_chat(input_data)

    async def process_new_complaint(self, complaint_id: str, complaint_data: dict) -> dict:
        """
        Full agent pipeline for a new complaint:
        Triage → Sentiment → Routing → [Escalation Check] → Notification
        """
        log.info("Starting complaint pipeline", complaint_id=complaint_id)

        pipeline_result = {"complaint_id": complaint_id, "pipeline_stages": {}}

        # Stage 1: Triage (parallel with sentiment for speed)
        triage_task = self.triage.run(complaint_data, complaint_id)
        sentiment_task = self.sentiment.run(complaint_data, complaint_id)

        triage_result, sentiment_result = await asyncio.gather(triage_task, sentiment_task)

        pipeline_result["pipeline_stages"]["triage"] = triage_result
        pipeline_result["pipeline_stages"]["sentiment"] = sentiment_result

        # Merge results for routing
        enriched = {**complaint_data, **triage_result, **sentiment_result}

        # Stage 2: Routing
        routing_result = await self.routing.run(enriched, complaint_id)
        pipeline_result["pipeline_stages"]["routing"] = routing_result

        # Stage 3: Update MongoDB with AI enrichments
        update_doc = {
            "ai_category": triage_result.get("ai_category", "other"),
            "ai_summary": triage_result.get("ai_summary", ""),
            "ai_sentiment": sentiment_result.get("ai_sentiment", "neutral"),
            "ai_priority": sentiment_result.get("ai_priority", "p3_medium"),
            "urgency_score": sentiment_result.get("urgency_score", 0.5),
            "ai_suggested_department": routing_result.get("ai_suggested_department", "admin"),
            "agent_processed": True,
            "updated_at": datetime.now(timezone.utc),
            "priority": sentiment_result.get("ai_priority", "p3_medium"),
            "needs_escalation": triage_result.get("needs_escalation", False),
            "escalation_reason": triage_result.get("escalation_reason"),
        }

        try:
            col = mongo.get_collection("complaints")
            await col.update_one(
                {"_id": ObjectId(complaint_id)},
                {"$set": update_doc},
            )
            log.info("Complaint enriched in MongoDB", complaint_id=complaint_id)
        except Exception as e:
            log.error("Failed to update complaint", error=str(e))

        # Stage 4: Escalation check
        if triage_result.get("needs_escalation") or sentiment_result.get("urgency_score", 0) > 0.85:
            escalation_result = await self.notification.send_escalation_alert(
                {**complaint_data, **update_doc},
                triage_result.get("escalation_reason", "High urgency detected"),
            )
            pipeline_result["pipeline_stages"]["escalation"] = escalation_result

            # Mark as escalated in DB
            await col.update_one(
                {"_id": ObjectId(complaint_id)},
                {"$set": {"escalated": True, "status": "escalated"}},
            )

        # Stage 5: Send confirmation notification to student
        notification_input = {
            **complaint_data,
            "complaint_id": complaint_id,
            "old_status": "none",
            "new_status": "open",
            "event_type": "complaint_received",
            "admin_reply": "Your complaint has been received and is being processed by our AI system.",
        }
        notification_result = await self.notification.run(notification_input, complaint_id)
        pipeline_result["pipeline_stages"]["notification"] = notification_result

        pipeline_result["success"] = True
        pipeline_result["enriched_data"] = update_doc

        log.info("Complaint pipeline completed", complaint_id=complaint_id)
        return pipeline_result

    async def process_complaint_update(self, complaint_id: str, update_data: dict) -> dict:
        """Handle complaint status update - sends notification to student."""
        notification_input = {
            **update_data,
            "complaint_id": complaint_id,
            "event_type": "status_update",
        }
        return await self.notification.run(notification_input, complaint_id)

    async def _classify_intent(self, input_data: dict) -> dict:
        """Use Gemini to classify the intent of incoming request."""
        request = input_data.get("question") or input_data.get("title") or str(input_data)

        try:
            raw = await self.gemini_generate(
                INTENT_PROMPT.format(request=request[:500])
            )
            return self.parse_json_response(raw)
        except Exception:
            return {
                "intent": "chat_query",
                "confidence": 0.5,
                "requires_agents": ["chatbot"],
            }

    async def _process_new_complaint(self, input_data: dict) -> dict:
        complaint_id = input_data.get("complaint_id")
        if complaint_id:
            return await self.process_new_complaint(complaint_id, input_data)
        return {"error": "No complaint_id provided", "intent": "new_complaint"}

    async def _process_chat(self, input_data: dict) -> dict:
        return await self.chatbot.run(input_data)

    async def _process_analytics(self, input_data: dict) -> dict:
        return await self.analytics.run(input_data)

    async def _process_status_query(self, input_data: dict) -> dict:
        """Query complaint status from MongoDB."""
        user_id = input_data.get("user_id")
        if not user_id:
            return await self._process_chat(input_data)

        try:
            col = mongo.get_collection("complaints")
            complaints = await col.find(
                {"user_id": user_id},
                {"title": 1, "status": 1, "created_at": 1, "ai_summary": 1, "admin_reply": 1},
            ).sort("created_at", -1).limit(5).to_list(5)

            return {
                "intent": "status_query",
                "complaints": [
                    {
                        "id": str(c["_id"]),
                        "title": c.get("title"),
                        "status": c.get("status"),
                        "summary": c.get("ai_summary"),
                        "admin_reply": c.get("admin_reply"),
                        "created_at": c.get("created_at", "").isoformat() if c.get("created_at") else "",
                    }
                    for c in complaints
                ],
            }
        except Exception as e:
            return {"error": str(e)}


# Global orchestrator instance
orchestrator = OrchestratorAgent()
