"""
BIT Updates v2 - Base Agent
All AI agents inherit from this class.
Uses Gemini 2.0 Flash via google-generativeai SDK.
"""

import time
import asyncio
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Optional
import google.generativeai as genai
import structlog

from backend.utils.config import settings
from backend.utils.database import mongo, agent_log_document, COLLECTIONS

log = structlog.get_logger(__name__)

# Configure Gemini globally
genai.configure(api_key=settings.GEMINI_API_KEY)


class BaseAgent(ABC):
    """
    Base class for all BIT Updates AI agents.
    Provides: Gemini 2.0 Flash integration, MongoDB logging,
    retry logic, and structured output parsing.
    """

    MODEL = "gemini-2.0-flash-exp"
    MAX_RETRIES = 3
    TIMEOUT_SECONDS = 30

    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description
        self.model = genai.GenerativeModel(
            model_name=self.MODEL,
            generation_config=genai.GenerationConfig(
                temperature=0.2,
                top_p=0.95,
                max_output_tokens=2048,
            ),
        )
        self.log = log.bind(agent=self.name)

    @abstractmethod
    async def process(self, input_data: dict) -> dict:
        """Main agent processing logic. Override in subclasses."""
        ...

    async def run(self, input_data: dict, complaint_id: str = None) -> dict:
        """
        Execute agent with logging, timing, and error handling.
        Returns result dict always (never raises to caller).
        """
        start_time = time.perf_counter()
        self.log.info("Agent starting", complaint_id=complaint_id)

        try:
            result = await self.process(input_data)
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            result["_agent_success"] = True
            result["_agent_ms"] = round(elapsed_ms, 2)

            # Log to MongoDB
            if complaint_id:
                await self._log_to_db(complaint_id, input_data, result, elapsed_ms, True)

            self.log.info("Agent completed", ms=round(elapsed_ms, 2))
            return result

        except Exception as e:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            self.log.error("Agent failed", error=str(e))

            error_result = {
                "_agent_success": False,
                "_agent_error": str(e),
                "_agent_ms": round(elapsed_ms, 2),
            }

            if complaint_id:
                await self._log_to_db(complaint_id, input_data, error_result, elapsed_ms, False, str(e))

            return error_result

    async def gemini_generate(self, prompt: str) -> str:
        """Call Gemini with retry logic."""
        for attempt in range(self.MAX_RETRIES):
            try:
                response = await asyncio.wait_for(
                    asyncio.to_thread(self.model.generate_content, prompt),
                    timeout=self.TIMEOUT_SECONDS,
                )
                return response.text.strip()
            except asyncio.TimeoutError:
                self.log.warning("Gemini timeout", attempt=attempt + 1)
                if attempt == self.MAX_RETRIES - 1:
                    raise
                await asyncio.sleep(2 ** attempt)
            except Exception as e:
                self.log.warning("Gemini error", error=str(e), attempt=attempt + 1)
                if attempt == self.MAX_RETRIES - 1:
                    raise
                await asyncio.sleep(2 ** attempt)

    async def _log_to_db(
        self,
        complaint_id: str,
        input_data: dict,
        output_data: dict,
        elapsed_ms: float,
        success: bool,
        error: str = None,
    ):
        """Persist agent execution log to MongoDB."""
        try:
            doc = agent_log_document(
                agent_name=self.name,
                complaint_id=complaint_id,
                input_data=self._sanitize(input_data),
                output_data=self._sanitize(output_data),
                execution_time_ms=elapsed_ms,
                success=success,
                error=error,
            )
            col = mongo.get_collection("agent_logs")
            result = await col.insert_one(doc)

            # Link log ID back to complaint
            await mongo.get_collection("complaints").update_one(
                {"_id": complaint_id},
                {"$push": {"agent_log_ids": str(result.inserted_id)}},
            )
        except Exception as e:
            self.log.error("Failed to log agent execution", error=str(e))

    def _sanitize(self, data: dict) -> dict:
        """Remove non-serializable objects for MongoDB storage."""
        if not isinstance(data, dict):
            return {}
        sanitized = {}
        for k, v in data.items():
            if isinstance(v, (str, int, float, bool, list, dict, type(None))):
                sanitized[k] = v
            else:
                sanitized[k] = str(v)
        return sanitized

    def parse_json_response(self, text: str) -> dict:
        """Safely parse JSON from Gemini response."""
        import json
        import re
        # Extract JSON from code block if present
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if match:
            text = match.group(1)
        # Try to find bare JSON object
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            text = match.group(0)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {}
