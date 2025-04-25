import ast
import json
import re


def get_crew_output(result):

    def extract_json_block(text):
        try:
            match = re.search(r'{.*}', text, re.DOTALL)
            return json.loads(match.group(0)) if match else {}
        except Exception:
            return {}

    try:
        if isinstance(result, dict):
            return result
        elif isinstance(result, str):
            return json.loads(result)
        elif hasattr(result, 'result'):
            return extract_json_block(result.result)
        elif hasattr(result, 'output'):
            return extract_json_block(result.output)
        elif isinstance(result, list) and hasattr(result[0], 'output'):
            return extract_json_block(result[0].output)
        return {}
    except Exception as e:
        print("⚠️ CrewAI output parsing failed:", e)
        return {}


def extract_json_block(text):
    try:
        match = re.search(r"```(?:json)?\s*({.*?})\s*```", text, re.DOTALL)
        raw_json = match.group(1) if match else text.strip()
        try:
            return json.loads(raw_json)
        except json.JSONDecodeError:
            return ast.literal_eval(raw_json)  # fallback for Python-style dicts
    except Exception as e:
        print(f"❌ Failed to extract JSON block: {e}")
        return {}


def ensure_dict(value):
    if isinstance(value, dict):
        return value
    try:
        return json.loads(value)
    except:
        return {}
