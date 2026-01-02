from __future__ import annotations

from typing import Callable, Dict, Optional

from core.models import ActionStep, Command, Result
from core.intent import Intent, Mode


SkillHandler = Callable[[ActionStep], Result]
ChatHandler = Callable[[Command], Result]


class Router:
    def __init__(self) -> None:
        self._chat_handler: Optional[ChatHandler] = None
        self._action_routes: Dict[Intent, SkillHandler] = {}

    def register_chat(self, handler: ChatHandler) -> None:
        self._chat_handler = handler

    def register_action(self, intent: Intent, handler: SkillHandler) -> None:
        self._action_routes[intent] = handler

    def dispatch_chat(self, cmd: Command) -> Result:
        if self._chat_handler is None:
            return Result(ok=False, message="No chat handler registered.")
        return self._chat_handler(cmd)

    def dispatch_step(self, step: ActionStep) -> Result:
        handler = self._action_routes.get(step.intent)
        if handler is None:
            return Result(ok=False, message=f"No skill registered for intent {step.intent.value}.")
        return handler(step)
