"""
BIT Updates v2 - Analytics Agent
Computes trends, predictions, and campus health insights from MongoDB data.
Provides department performance, complaint volume forecasting, and hotspot detection.
"""

from datetime import datetime, timezone, timedelta
from collections import defaultdict, Counter
from typing import Any
from bson import ObjectId

from backend.agents.base_agent import BaseAgent
from backend.utils.database import mongo


ANALYTICS_PROMPT = """
You are an Analytics & Insights Agent for a campus support system.
Based on the following complaint statistics, generate actionable insights.

Statistics:
{stats_json}

Provide a JSON response with these fields:
{{
  "executive_summary": "<2-3 sentence overview of campus health>",
  "top_issues": ["<top 3 recurring issue patterns>"],
  "department_alerts": [
    {{
      "department": "<dept name>",
      "alert": "<specific concern>",
      "severity": "<critical|warning|info>"
    }}
  ],
  "trend_analysis": "<paragraph describing trends>",
  "predicted_volume_next_week": <integer estimate>,
  "recommended_actions": ["<list of 3 actionable recommendations for admin>"],
  "campus_health_score": <integer 0-100, 100 being best>,
  "resolution_efficiency": "<assessment of how quickly issues are resolved>",
  "student_satisfaction_estimate": "<low|medium|high>"
}}

Return ONLY the JSON object.
"""


class AnalyticsAgent(BaseAgent):
    """
    Analytics Agent: Trends & Predictions
    Aggregates MongoDB data and uses Gemini to generate insights.
    """

    def __init__(self):
        super().__init__(
            name="AnalyticsAgent",
            description="Computes trends, predictions, and campus health insights",
        )

    async def process(self, input_data: dict) -> dict:
        """Generate comprehensive analytics report."""
        days = input_data.get("days", 30)

        # Run all aggregations in parallel
        stats = await self._aggregate_stats(days)

        # Get AI insights
        import json
        prompt = ANALYTICS_PROMPT.format(stats_json=json.dumps(stats, indent=2, default=str))

        try:
            raw = await self.gemini_generate(prompt)
            insights = self.parse_json_response(raw)
        except Exception:
            insights = {
                "executive_summary": "Analytics data collected successfully.",
                "campus_health_score": 70,
                "top_issues": [],
                "department_alerts": [],
                "recommended_actions": [],
            }

        return {
            "stats": stats,
            "insights": insights,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "period_days": days,
        }

    async def _aggregate_stats(self, days: int) -> dict:
        """Run MongoDB aggregation pipelines for analytics."""
        col = mongo.get_collection("complaints")
        since = datetime.now(timezone.utc) - timedelta(days=days)

        # Total counts by status
        status_pipeline = [
            {"$match": {"created_at": {"$gte": since}}},
            {"$group": {"_id": "$status", "count": {"$sum": 1}}},
        ]
        status_results = await col.aggregate(status_pipeline).to_list(20)
        by_status = {r["_id"]: r["count"] for r in status_results}

        # By department
        dept_pipeline = [
            {"$match": {"created_at": {"$gte": since}}},
            {"$group": {
                "_id": "$department",
                "count": {"$sum": 1},
                "resolved": {"$sum": {"$cond": [{"$eq": ["$status", "resolved"]}, 1, 0]}},
                "avg_priority": {"$avg": "$urgency_score"},
            }},
            {"$sort": {"count": -1}},
            {"$limit": 10},
        ]
        dept_results = await col.aggregate(dept_pipeline).to_list(10)

        # By category
        cat_pipeline = [
            {"$match": {"created_at": {"$gte": since}, "ai_category": {"$ne": None}}},
            {"$group": {"_id": "$ai_category", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 8},
        ]
        cat_results = await col.aggregate(cat_pipeline).to_list(8)

        # Daily volume (last 14 days)
        daily_pipeline = [
            {"$match": {"created_at": {"$gte": datetime.now(timezone.utc) - timedelta(days=14)}}},
            {"$group": {
                "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$created_at"}},
                "count": {"$sum": 1},
            }},
            {"$sort": {"_id": 1}},
        ]
        daily_results = await col.aggregate(daily_pipeline).to_list(14)

        # Resolution time stats
        resolution_pipeline = [
            {"$match": {"status": "resolved", "resolved_at": {"$ne": None}, "created_at": {"$gte": since}}},
            {"$project": {
                "resolution_hours": {
                    "$divide": [
                        {"$subtract": ["$resolved_at", "$created_at"]},
                        3600000,  # ms to hours
                    ]
                }
            }},
            {"$group": {
                "_id": None,
                "avg_hours": {"$avg": "$resolution_hours"},
                "min_hours": {"$min": "$resolution_hours"},
                "max_hours": {"$max": "$resolution_hours"},
                "count": {"$sum": 1},
            }},
        ]
        resolution_results = await col.aggregate(resolution_pipeline).to_list(1)

        total = sum(by_status.values())
        resolved = by_status.get("resolved", 0)

        return {
            "period_days": days,
            "total_complaints": total,
            "by_status": by_status,
            "resolution_rate": round(resolved / max(total, 1) * 100, 1),
            "by_department": [
                {
                    "department": r["_id"] or "Unknown",
                    "total": r["count"],
                    "resolved": r["resolved"],
                    "resolution_rate": round(r["resolved"] / max(r["count"], 1) * 100, 1),
                    "avg_urgency": round(r.get("avg_priority", 0.5), 2),
                }
                for r in dept_results
            ],
            "by_category": [{"category": r["_id"], "count": r["count"]} for r in cat_results],
            "daily_volume": [{"date": r["_id"], "count": r["count"]} for r in daily_results],
            "resolution_stats": resolution_results[0] if resolution_results else {
                "avg_hours": 0, "min_hours": 0, "max_hours": 0, "count": 0
            },
        }

    async def get_department_report(self, department: str, days: int = 30) -> dict:
        """Department-specific analytics."""
        col = mongo.get_collection("complaints")
        since = datetime.now(timezone.utc) - timedelta(days=days)

        pipeline = [
            {"$match": {"department": department, "created_at": {"$gte": since}}},
            {"$group": {
                "_id": "$status",
                "count": {"$sum": 1},
                "avg_urgency": {"$avg": "$urgency_score"},
            }},
        ]
        results = await col.aggregate(pipeline).to_list(10)

        return {
            "department": department,
            "period_days": days,
            "by_status": {r["_id"]: r["count"] for r in results},
            "avg_urgency": round(
                sum(r.get("avg_urgency", 0.5) * r["count"] for r in results) /
                max(sum(r["count"] for r in results), 1),
                2,
            ),
        }
