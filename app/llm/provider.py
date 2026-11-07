from typing import Protocol


class LLMProvider(Protocol):
    model: str

    async def generate(self, system: str, prompt: str, schema: dict) -> str: ...
