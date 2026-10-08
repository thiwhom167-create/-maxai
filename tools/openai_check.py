import os
import sys
import requests


def check_openai(prompt="ping", timeout=30):
    base = os.environ.get("AI_URL", "https://api.openai.com/v1").rstrip("/")
    key = os.environ.get("AI_KEY", "")
    model = os.environ.get("AI_MODEL", "")

    result = {
        "ok": False,
        "provider_url": base,
        "model": model,
        "has_key": bool(key),
    }

    if not key:
        result["error"] = "AI_KEY is missing"
        return result

    try:
        r = requests.get(
            base + "/models",
            headers={"Authorization": "Bearer " + key},
            timeout=timeout,
        )
        result["models_status"] = r.status_code
        if r.status_code != 200:
            try:
                body = r.json()
                result["error"] = body.get("error", {}).get("message", "models request failed")
            except Exception:
                result["error"] = "models request failed"
            return result

        if not model:
            result["error"] = "AI_MODEL is missing"
            return result

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
        }
        r = requests.post(
            base + "/chat/completions",
            headers={
                "Authorization": "Bearer " + key,
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=timeout,
        )
        result["chat_status"] = r.status_code

        try:
            data = r.json()
        except Exception:
            data = {}

        if r.status_code == 200 and data.get("choices"):
            result["ok"] = True
            result["response"] = data["choices"][0]["message"].get("content", "")
            result["response_model"] = data.get("model", model)
            return result

        result["error"] = data.get("error", {}).get(
            "message", "chat completion failed"
        )
        return result

    except requests.RequestException as exc:
        result["error"] = type(exc).__name__ + ": " + str(exc)
        return result


if __name__ == "__main__":
    result = check_openai(os.environ.get("OPENAI_TEST_PROMPT", "Reply only: OK"))
    print(result)
    sys.exit(0 if result.get("ok") else 1)
