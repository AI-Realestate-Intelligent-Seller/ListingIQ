from .rag import rag_service
from module.prompt import prompt
from datetime import datetime
from .db import SessionLocal
from .models import AiRun

class AiRuntime:
    def __init__(self):
        self.rag = rag_service

    def build_prompt(self, conversation_history: list[str], next_step: str):
        query = ' '.join(conversation_history[-5:] + [next_step])
        context = self.rag.prompt_context(query)
        system_prompt = prompt.get('bobbie_system_prompt', '')
        instructions = prompt.get('scheduling_instructions', '')
        context_text = '\n'.join([str(item) for item in context]) if context else 'No additional context available.'
        full_prompt = (
            f"{system_prompt}\n\n"
            f"Known context:\n{context_text}\n\n"
            f"Conversation history:\n" + '\n'.join(conversation_history[-10:]) + f"\n\nNext step: {next_step}\n\n{instructions}"
        )
        return full_prompt

    def generate_reply(self, conversation_history: list[str], next_step: str, user_id: int | None = None):
        prompt_text = self.build_prompt(conversation_history, next_step)
        result = f"[AI suggested reply based on conversation history and indexed context]\n{prompt_text[:1024]}"
        if user_id:
            self._log_run(user_id, 'local-rag', prompt_text, result)
        return result

    def _log_run(self, user_id: int, model: str, prompt_text: str, result: str):
        session = SessionLocal()
        try:
            run = AiRun(user_id=user_id, model=model, prompt=prompt_text, result=result, created_at=datetime.utcnow())
            session.add(run)
            session.commit()
        finally:
            session.close()

ai_runtime = AiRuntime()
