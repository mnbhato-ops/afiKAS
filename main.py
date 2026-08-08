import json
import os
import re
import sys
import time
import requests
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

POSTED_ASINS_FILE = "posted_asins.json"


class ProductRecommendation(BaseModel):
    asin: str = Field(description="Amazon ASIN (10桁の英数字, 例: B08N5WRWNW)")
    product_name: str = Field(description="選定した商品の正式名称")
    post_text: str = Field(
        description="Threads用の投稿文（200〜400文字程度。30-50代女性向け共感文、メリット・使用感、ハッシュタグ #PR を含む）"
    )


def load_posted_asins(filepath: str) -> list[str]:
    """過去に投稿したASINリストをJSONから読み込む"""
    if not os.path.exists(filepath):
        return []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
            return []
    except Exception as e:
        print(f"Warning: Failed to load {filepath}: {e}")
        return []


def save_posted_asins(filepath: str, asins: list[str]) -> None:
    """投稿済みASINリストをJSONに保存する"""
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(asins, f, ensure_ascii=False, indent=2)


def fetch_product_candidate(api_key: str, posted_asins: list[str]) -> ProductRecommendation:
    """
    Gemini APIを使用して30〜50代女性向けのおすすめ商品と投稿本文を生成する。
    429エラーが発生した場合はSearch Groundingなしのノーマルモードおよびモデル切替へフォールバックする。
    """
    client = genai.Client(api_key=api_key)
    excluded_str = ", ".join(posted_asins) if posted_asins else "なし"

    prompt = f"""
あなたは30〜50代女性に絶大な支持を得る人気ライフスタイルブロガー・商品キュレーターです。

【目的】
Amazon.co.jpで話題・人気となっている「30〜50代女性」ターゲットの良質な商品を1つ厳選し、Threads投稿用データを作成してください。

【ターゲット層の興味・関心領域】
- 忙しい毎日の負担を減らす「時短家電」「キッチンの便利ツール」
- 30〜50代女性向けの「高品質スキンケア」「美容・ヘアケア用品」
- 日々の「疲労回復」「ヘルスケア」「睡眠改善グッズ」
- 日常を少し豊かにする「プチ贅沢品」「自分へのご褒美アイテム」

【過去に投稿済みの除外ASINリスト】
{excluded_str}
※上記リストに含まれるASIN（過去紹介済み商品）は絶対に除外してください。

【選定手順】
1. ターゲット層（30〜50代女性）に人気・高評価の実在する10桁英数字ASIN（例: B0XXXXXXXX など）を選定してください。
2. Amazon.co.jpで正しく検索可能な商品を選択してください。

【Threads投稿文作成ルール】
- 文字数: 日本語で200文字〜400文字程度
- 1行目: ターゲット層の悩みや感性に響くキャッチーな見出し
- 本文: 具体的なメリット、使用感、どんな悩みが解消されるかへの共感
- ハッシュタグ: 商品に関連するハッシュタグに加え、広告表記「#PR」を必ず含めてください。
"""

    attempts_configs = [
        {"name": "gemini-3.5-flash with Grounding", "model": "gemini-2.5-flash", "use_grounding": True},
        {"name": "gemini-3.5-flash Standard (Fallback)", "model": "gemini-2.5-flash", "use_grounding": False},
        {"name": "gemini-3.5-flash Standard (Fallback)", "model": "gemini-1.5-flash", "use_grounding": False},
    ]

    for attempt, attempt_info in enumerate(attempts_configs):
        print(f"Fetching recommendation via Gemini API ({attempt_info['name']}) [Attempt {attempt+1}/3]...")
        
        if attempt_info["use_grounding"]:
            config = types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                response_mime_type="application/json",
                response_schema=ProductRecommendation,
                temperature=0.7,
            )
        else:
            config = types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=ProductRecommendation,
                temperature=0.7,
            )

        try:
            response = client.models.generate_content(
                model=attempt_info["model"],
                contents=prompt,
                config=config,
            )

            res_data = None
            if getattr(response, "parsed", None) and isinstance(response.parsed, ProductRecommendation):
                res_data = response.parsed
            else:
                text = response.text.strip()
                match = re.search(r"\{.*\}", text, re.DOTALL)
                json_str = match.group(0) if match else text
                data = json.loads(json_str)
                res_data = ProductRecommendation(**data)

            asin = res_data.asin.strip().upper()

            # ASINフォーマット検証（10桁英数字）
            if not re.match(r"^[A-Z0-9]{10}$", asin):
                print(f"Invalid ASIN format received: '{asin}'. Retrying...")
                time.sleep(3)
                continue

            # 重複チェック
            if asin in posted_asins:
                print(f"ASIN '{asin}' was already posted in past. Retrying for another product...")
                time.sleep(3)
                continue

            res_data.asin = asin
            return res_data

        except Exception as e:
            print(f"Error on attempt {attempt+1} ({attempt_info['name']}): {e}")
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                print("429 Quota Exceeded. Trying next configuration...")
                time.sleep(5)
            else:
                time.sleep(3)

    raise ValueError("Failed to obtain a valid, non-duplicate product recommendation after maximum retries. Please check your Gemini API Key quota.")


def create_threads_post(user_id: str, access_token: str, text: str, link_url: str) -> str:
    """Threads Graph APIを使用して投稿メディアコンテナを作成する"""
    target = user_id if user_id and user_id != "me" else "me"
    url = f"https://graph.threads.net/v1.0/{target}/threads"

    payload = {
        "media_type": "TEXT",
        "text": text,
        "link_attachment": link_url,
        "access_token": access_token,
    }

    print(f"Creating Threads media container (User: {target})...")
    response = requests.post(url, data=payload, timeout=30)
    res_data = response.json()

    if response.status_code != 200 or "id" not in res_data:
        print(f"Error creating Threads container: Status {response.status_code}, Response: {res_data}")
        sys.exit(1)

    container_id = res_data["id"]
    print(f"Media container created successfully. ID: {container_id}")
    return container_id


def publish_threads_post(user_id: str, access_token: str, creation_id: str) -> str:
    """Threads Graph APIを使用してコンテナを本投稿として公開する"""
    target = user_id if user_id and user_id != "me" else "me"
    url = f"https://graph.threads.net/v1.0/{target}/threads_publish"

    payload = {
        "creation_id": creation_id,
        "access_token": access_token,
    }

    print(f"Publishing Threads post (Creation ID: {creation_id})...")
    response = requests.post(url, data=payload, timeout=30)
    res_data = response.json()

    if response.status_code != 200 or "id" not in res_data:
        print(f"Error publishing Threads post: Status {response.status_code}, Response: {res_data}")
        sys.exit(1)

    published_id = res_data["id"]
    print(f"Threads post published successfully! Published Post ID: {published_id}")
    return published_id


def main():
    gemini_api_key = os.environ.get("GEMINI_KEY")
    associate_tag = os.environ.get("AMAZON_ID")
    threads_user_id = os.environ.get("THREADS_USER_ID", "me")
    threads_access_token = os.environ.get("THREADS_ACCESS_TOKEN")

    missing_vars = []
    if not gemini_api_key:
        missing_vars.append("GEMINI_KEY")
    if not associate_tag:
        missing_vars.append("AMAZON_ID")
    if not threads_access_token:
        missing_vars.append("THREADS_ACCESS_TOKEN")

    if missing_vars:
        print(f"Error: Missing required environment variables: {', '.join(missing_vars)}")
        sys.exit(1)

    # 1. 過去ログの読み込み
    posted_asins = load_posted_asins(POSTED_ASINS_FILE)
    print(f"Loaded {len(posted_asins)} previously posted ASINs.")

    # 2. Gemini API による商品リサーチと投稿文作成
    recommendation = fetch_product_candidate(gemini_api_key, posted_asins)
    print(f"Selected Product: {recommendation.product_name}")
    print(f"Selected ASIN: {recommendation.asin}")

    # 3. AmazonアフィリエイトURLの生成
    affiliate_url = f"https://www.amazon.co.jp/dp/{recommendation.asin}?tag={associate_tag}"
    print(f"Generated Affiliate URL: {affiliate_url}")

    # 4. 投稿プレビュー表示
    print("\n--- Threads Post Content Preview ---")
    print(recommendation.post_text)
    print("------------------------------------\n")

    # 5. Threads API による自動投稿
    creation_id = create_threads_post(
        user_id=threads_user_id,
        access_token=threads_access_token,
        text=recommendation.post_text,
        link_url=affiliate_url,
    )

    publish_threads_post(
        user_id=threads_user_id,
        access_token=threads_access_token,
        creation_id=creation_id,
    )

    # 6. 履歴の更新と保存
    posted_asins.append(recommendation.asin)
    save_posted_asins(POSTED_ASINS_FILE, posted_asins)
    print(f"Successfully updated {POSTED_ASINS_FILE} with new ASIN: {recommendation.asin}")


if __name__ == "__main__":
    main()
