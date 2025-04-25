import requests


class OllamaModel:
    def __init__(self, model: str, base_url: str = "http://localhost:11434"):
        self.model = model
        self.url = f"{base_url}/api/generate"

    def invoke(self, prompt: str) -> str:
        response = requests.post(self.url, json={
            "model": self.model,
            "prompt": prompt,
            "stream": False
        })

        if response.status_code != 200:
            raise RuntimeError(f"Ollama error: {response.status_code} {response.text}")

        result = response.json()
        return result.get("response", "").strip()
