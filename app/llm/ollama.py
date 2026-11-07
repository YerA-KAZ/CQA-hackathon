import httpx


class OllamaProvider:
    def __init__(self, client: httpx.AsyncClient, url: str, model: str):
        self.client = client
        self.url = url.rstrip("/")
        self.model = model

    async def generate(self, system: str, prompt: str, schema: dict) -> str:
        response = await self.client.post(
            f"{self.url}/api/chat",
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "format": schema,
                "options": {"temperature": 0},
                "think": False,
                "stream": False,
            },
        )
        response.raise_for_status()
        return response.json()["message"]["content"]
