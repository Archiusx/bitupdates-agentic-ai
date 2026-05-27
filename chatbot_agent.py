"""
BIT Updates v2 - Chatbot Agent
RAG-powered FAQ assistant for students.
Searches MongoDB for similar past complaints and campus knowledge base.
Uses Retrieval Augmented Generation for grounded answers.
"""

from datetime import datetime, timezone
from backend.agents.base_agent import BaseAgent
from backend.utils.database import mongo


# Campus FAQ knowledge base (augmented by MongoDB historical data)
CAMPUS_FAQ = {
    "exam_schedule": "Exam schedules are posted on the academic portal 2 weeks before exams. Check the Examination Cell section.",
    "library_hours": "Central Library is open 8 AM - 10 PM on weekdays, 9 AM - 6 PM on weekends.",
    "hostel_rules": "Hostel curfew is 10 PM. Gate passes must be requested 24 hours in advance from the warden.",
    "fee_payment": "Fees can be paid online via the student portal or at the Finance Office (Mon-Sat, 10 AM - 4 PM).",
    "wifi_issues": "Campus WiFi issues should be reported to IT Support (it.support@bitwardha.ac.in or ext. 234).",
    "attendance": "Minimum 75% attendance is mandatory. Attendance data is updated every Friday on the portal.",
    "canteen_timing": "Main canteen operates 7:30 AM - 9 PM. Mini canteen near library: 8 AM - 6 PM.",
    "transport": "College buses run on fixed routes. Schedule is posted on the transport office notice board.",
    "sports": "Sports facilities are available 5-7 PM weekdays and 7 AM - 6 PM weekends.",
    "placement": "T&P cell registration opens in July. Keep your resume and academic transcripts ready.",
}

CHATBOT_PROMPT = """
You are a helpful AI assistant for BIT Wardha campus student support system.
Your name is "BITS" (BIT Intelligent Support System).

You help students with:
- Answering questions about campus facilities, rules, and procedures
- Providing guidance on complaint status and processes
- Giving information about departments and contacts
- Explaining college policies

Campus FAQ Knowledge Base:
{faq_context}

Similar Past Resolved Complaints:
{similar_complaints}

Student's Question: {question}
Student Name: {student_name}

Guidelines:
- Be friendly, professional, and empathetic
- Keep responses concise (2-4 paragraphs max)
- If you don't know something specific, direct them to the right department
- Always end with a helpful suggestion or next step
- Use markdown formatting for clarity
- Never make up specific dates, names, or policy details not in your knowledge base

Provide a helpful, accurate response:
"""

FALLBACK_RESPONSE = """
Hello! I'm BITS, your campus support assistant. 

I couldn't find a specific answer to your question in my knowledge base. Here's what I suggest:

1. **Check the relevant department**: Visit the respective department office during working hours (9 AM - 5 PM)
2. **Submit a ticket**: Use the complaint system to formally raise your issue
3. **Contact Admin**: admin@bitwardha.ac.in for general queries

Is there anything else I can help you with? 😊
"""


class ChatbotAgent(BaseAgent):
    """
    Chatbot Agent: RAG + FAQ Assistant
    Combines MongoDB historical data with Gemini for grounded answers.
    """

    def __init__(self):
        super().__init__(
            name="ChatbotAgent",
            description="RAG-powered FAQ assistant with historical complaint context",
        )

    async def process(self, input_data: dict) -> dict:
        question = input_data.get("question", "")
        student_name = input_data.get("student_name", "Student")

        # Fetch similar resolved complaints from MongoDB (RAG context)
        similar = await self._fetch_similar_complaints(question)

        # Build FAQ context
        faq_context = "\n".join([f"- {k}: {v}" for k, v in CAMPUS_FAQ.items()])

        prompt = CHATBOT_PROMPT.format(
            faq_context=faq_context,
            similar_complaints=similar,
            question=question,
            student_name=student_name,
        )

        try:
            response = await self.gemini_generate(prompt)
            confidence = 0.85 if any(kw in question.lower() for kw in CAMPUS_FAQ.keys()) else 0.65
        except Exception:
            response = FALLBACK_RESPONSE
            confidence = 0.0

        # Log conversation to MongoDB
        await self._log_conversation(question, response, student_name, input_data.get("user_id"))

        return {
            "response": response,
            "confidence": confidence,
            "sources": list(CAMPUS_FAQ.keys()),
            "similar_complaint_count": len(similar.split("\n")) if similar else 0,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    async def _fetch_similar_complaints(self, question: str, limit: int = 3) -> str:
        """Fetch similar resolved complaints from MongoDB for RAG context."""
        try:
            col = mongo.get_collection("complaints")
            # Text search on complaints collection
            cursor = col.find(
                {
                    "$text": {"$search": question},
                    "status": "resolved",
                },
                {"title": 1, "ai_summary": 1, "admin_reply": 1, "score": {"$meta": "textScore"}},
            ).sort([("score", {"$meta": "textScore"})]).limit(limit)

            complaints = await cursor.to_list(length=limit)

            if not complaints:
                return "No similar resolved complaints found."

            lines = []
            for c in complaints:
                lines.append(
                    f"• Issue: {c.get('title', 'N/A')} | "
                    f"Summary: {c.get('ai_summary', 'N/A')} | "
                    f"Resolution: {c.get('admin_reply', 'Resolved')}"
                )
            return "\n".join(lines)

        except Exception as e:
            self.log.warning("Could not fetch similar complaints", error=str(e))
            return "No similar complaints found."

    async def _log_conversation(self, question: str, response: str, name: str, user_id: str = None):
        """Log chat interaction to MongoDB for analytics."""
        try:
            col = mongo.get_collection("analytics")
            await col.insert_one({
                "event_type": "chatbot_interaction",
                "user_id": user_id,
                "user_name": name,
                "question": question[:500],
                "response_length": len(response),
                "timestamp": datetime.now(timezone.utc),
            })
        except Exception:
            pass
