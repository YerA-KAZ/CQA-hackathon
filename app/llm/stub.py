from collections.abc import Iterable


class StubProvider:
    model = "stub"

    def __init__(self, responses: Iterable[str]):
        self.responses = iter(responses)

    async def generate(self, system: str, prompt: str, schema: dict) -> str:
        return next(self.responses)
