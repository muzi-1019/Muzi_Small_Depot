"""快速验证视觉模型可用性：向配置的 vision_model_name 发送一张测试图片，打印回复。"""
import sys, base64, struct, zlib, httpx

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
from app.core.config import settings


def _make_png(w: int = 32, h: int = 32) -> bytes:
    """用 struct+zlib 动态生成合法的 w×h 白色 RGB PNG，无需任何外部依赖。"""
    def chunk(tag: bytes, data: bytes) -> bytes:
        payload = tag + data
        return struct.pack(">I", len(data)) + payload + struct.pack(">I", zlib.crc32(payload) & 0xFFFFFFFF)
    sig  = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    raw  = (b"\x00" + b"\xFF\xFF\xFF" * w) * h  # filter=0 + RGB=白色，逐行
    idat = chunk(b"IDAT", zlib.compress(raw))
    iend = chunk(b"IEND", b"")
    return sig + ihdr + idat + iend

def main():
    base_url = (settings.openai_api_base or "").rstrip("/")
    api_key  = settings.openai_api_key or ""
    model    = settings.vision_model_name

    print(f"[配置]")
    print(f"  api_base  : {base_url}")
    print(f"  api_key   : {'*' * max(0, len(api_key) - 6) + api_key[-6:] if api_key else '(空)'}")
    print(f"  model     : {model}")
    print()

    if not base_url or not api_key:
        print("❌ openai_api_base 或 openai_api_key 未配置，无法测试。")
        return

    b64 = base64.b64encode(_make_png(32, 32)).decode()
    payload = {
        "model": model,
        "messages": [
            {"role": "user", "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{b64}"}},
                {"type": "text", "text": "这张图片是什么颜色？一句话即可。"},
            ]},
        ],
        "max_tokens": 64,
        "temperature": 0.1,
    }
    url     = f"{base_url}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}"}

    print(f"[发送请求] POST {url}")
    try:
        with httpx.Client(timeout=30.0, trust_env=False) as client:
            resp = client.post(url, headers=headers, json=payload)
        print(f"[HTTP 状态] {resp.status_code}")
        if resp.status_code == 200:
            reply = resp.json()["choices"][0]["message"]["content"].strip()
            print(f"✅ 模型回复: {reply}")
        else:
            print(f"❌ 错误响应: {resp.text[:500]}")
    except Exception as exc:
        print(f"❌ 请求异常: {exc}")

if __name__ == "__main__":
    main()
