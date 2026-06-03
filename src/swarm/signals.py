from __future__ import annotations

import json

from .blackboard import Blackboard
from .models import ModelCaller, Signal
from .worker_dispatch import call_model


def prioritize_signals(blackboard: Blackboard, signals: list[Signal],
                       caller: ModelCaller) -> int:
    if not signals:
        return 0
    descs = [
        {"id": s.id, "content": s.content, "type": s.type}
        for s in signals[:20]
    ]
    prompt = (
        f'Assign priority to each signal for the task: "{blackboard.task_instruction}"\n\n'
        f"Signals: {json.dumps(descs, indent=2)}\n\n"
        "For each: critical (must address), high (important), medium (helpful), low (minor).\n"
        'Return: {"priorities": [{"id": "s1", "priority": "critical|high|medium|low"}]}'
    )
    payload, tokens = call_model(caller, prompt, max_tokens=2048)
    pmap = {
        p["id"]: p["priority"]
        for p in payload.get("priorities", [])
        if isinstance(p, dict) and "id" in p and "priority" in p
    }
    valid_priorities = {"critical", "high", "medium", "low"}
    for s in signals:
        if s.id in pmap and pmap[s.id] in valid_priorities:
            s.priority = pmap[s.id]
    return tokens
