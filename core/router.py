from __future__ import annotations

from typing import Callable, Dict, Optional
from core.models import ActionStep, Command, Result
from core.intent import Intent


SkillHandler = Callable[[ActionStep], Result]
ChatHandler = Callable[[Command], Result]


class Router:
    def __init__(self) -> None:
        self._action_routes: Dict[Intent, SkillHandler] = {}
        

    def register_action(self, intent: Intent, handler: SkillHandler) -> None:
        self._action_routes[intent] = handler       

    def dispatch_step(self, step: ActionStep) -> Result:
        handler = self._action_routes.get(step.intent)
        if handler is None:
            return Result(ok=False, message=f"No skill registered for intent {step.intent.value}.")
        return handler(step)
