import json
import os
import random
import re
import sys
import time
import requests
from pydantic import BaseModel, Field
from google import genai

POSTED_ASINS_FILE = "posted_asins.json"

# 30〜50代女性向け 実在確定Amazonベストセラー商品リスト
VERIFIED_AMAZON_PRODUCTS = [
    {"asin": "B0CD7LKGVP", "name": "パナソニック ヘアドライヤー ナノケア", "category": "美容・ヘアケア"},
    {"asin": "B09G37P3P8", "name": "ティファール 電気ケトル 1.0L", "category": "時短・キッチン家電"},
    {"asin": "B089QPJYMN", "name": "Refa(リファ) ファインバブル S シャワーヘッド", "category": "美容・疲労回復"},
    {"asin": "B09H23K6H6", "name": "象印 ステンレスマグ 480ml シームレスせん", "category": "便利グッズ・日常"},
    {"asin": "B08BFRV1BG", "name": "ロクシタン(L'OCCITANE) ハンドクリーム ギフトセット", "category": "プチ贅沢・ご褒美"},
    {"asin": "B0C9Q4W6Z2", "name": "アイリスオーヤマ 電気圧力鍋 2.2L", "category": "時短・キッチン家電"},
    {"asin": "B0C4YCHKYV", "name": "オルビス(ORBIS) オルビスユー エッセンスローション", "category": "高品質スキンケア"},
    {"asin": "B08HCSY3X4", "name": "クナイプ(Kneipp) バスソルト 入浴剤 850g", "category": "疲労回復・ヘルスケア"},
    {"asin": "B09PDGQK86", "name": "アテックス ルルド マッサージクッション", "category": "疲労回復・ヘルスケア"},
    {"asin": "B0BP1ZVKMN", "name": "サーモス 保温弁当箱 スープジャー 400ml", "category": "キッチンの便利ツール"},
]


class ProductRecommendation(BaseModel):
    asin: str = Field(description="Amazon ASIN")
    product_name: str = Field(description="選定した商品の名称")
    post_text: str = Field(description="Threads用の投稿文")


def load_posted_asins(filepath: str) -> list[str]:
    if not os.path.exists(filepath):
        return []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception as e:
        print(f"Warning: Failed to load {filepath}: {e}")
        return []


def save_posted_asins(filepath: str, asins: list[str]) -> None:
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(asins, f, ensure_ascii=False, indent=2)


def generate_threads_copy(client: genai.Client, product_name: str, category: str) -> str:
    prompt = f"""
あなたは30〜50代女性に絶大な支持を得る人気ライフスタイルブロガーです。
対象商品: 「{product_name}」（カテゴリ: {category}）
ターゲット層（30〜50代女性）に深く刺さるThreads用の投稿文章を作成してください。
文字数: 日本語で200文字〜350文字程度。1行目に魅力的見出し、本文に具体的メリット、関連ハッシュタグと広告表記「#PR」を必ず含めること。
"""
    models = ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-1.5-flash"]
    for model in models:
        try:
            print(f"Generating copy with Gemini ({model})...")
            response = client.models.generate_content(model=model, contents=prompt)
            if response and response.text:
                return response.text.strip()
        except Exception as e:
            print(f"Model {model} failed: {e}")
            time.sleep(2)

    return f"【30〜50代女性におすすめ✨】\n「{product_name}」が生活を快適に！日頃頑張る自分へのご褒美や家事の効率アップに最適です♪\n\n#おすすめ商品 #{category} #時短 #プチ贅沢 #PR"


def fetch_product_candidate(api_key: str, posted_asins: list[str]) -> ProductRecommendation:
    client = genai.Client(api_key=api_key)
    unposted = [p for p in VERIFIED_AMAZON_PRODUCTS if p["asin"] not in posted_asins]

    if not unposted:
        print("All products posted once. Resetting loop...")
        unposted = VERIFIED_AMAZON_PRODUCTS

    selected = random.choice(unposted)
    asin = selected["asin"]
    product_name = selected["name"]
    category = selected["category"]

    print(f"Selected verified product: {product_name} (ASIN: {asin})")
    post_text = generate_threads_copy(client, product_name, category)

    return ProductRecommendation(
        asin=asin,
        product_name=product_name,
        post_text=post_text
    )


def create_threads_post(user_id: str, access_token: str, text: str, link_url: str) -> str:
    target = user_id if user_id and user_id != "me" else "me"
    url = f"https://graph.threads.net/v1.0/{target}/threads"

    # 本文内に青文字のタップ可能アフィリエイトリンクを配置
    full_text = text if link_url in text else f"{text}\n\n🛒詳細・購入はこちら👇\n{link_url}"

    # link_attachmentを外し、下部の灰色「amazon.co.jp」枠を非表示にしてスッキリ投稿
    payload = {
        "media_type": "TEXT",
        "text": full_text,
        "access_token": access_token,
    }

    print(f"Creating Threads container (User: {target})...")
    response = requests.post(url, data=payload, timeout=30)
    res_data = response.json()

    if response.status_code != 200 or "id" not in res_data:
        print(f"Threads API Error: Status {response.status_code}")
        print(f"Response details: {res_data}")
        sys.exit(1)

    container_id = res_data["id"]
    print(f"Container created. ID: {container_id}")
    return container_id


def publish_threads_post(user_id: str, access_token: str, creation_id: str) -> str:
    target = user_id if user_id and user_id != "me" else "me"
    url = f"https://graph.threads.net/v1.0/{target}/threads_publish"

    payload = {
        "creation_id": creation_id,
        "access_token": access_token,
    }

    print(f"Publishing Threads post (ID: {creation_id})...")
    response = requests.post(url, data=payload, timeout=30)
    res_data = response.json()

    if response.status_code != 200 or "id" not in res_data:
        print(f"Threads Publish Error: Status {response.status_code}")
        print(f"Response details: {res_data}")
        sys.exit(1)

    published_id = res_data["id"]
    print(f"Threads post published successfully! Post ID: {published_id}")
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
        print(f"Error: Missing required env vars: {', '.join(missing_vars)}")
        sys.exit(1)

    # 1. 過去ログの読み込み
    posted_asins = load_posted_asins(POSTED_ASINS_FILE)
    print(f"Loaded {len(posted_asins)} previously posted ASINs.")

    # 2. 実在確定商品から選定しGeminiでThreads投稿文作成
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
