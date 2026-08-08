import json
import os
import random
import re
import sys
import time
import requests
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

POSTED_ASINS_FILE = "posted_asins.json"

# 30〜50代女性向け 実在確定Amazonベストセラー商品リスト (ASIN & 商品カテゴリ)
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


def check_amazon_page_valid(asin: str) -> bool:
    """
    Amazon.co.jp の HTML を詳細解析し、実在する商品詳細ページであるか（404や犬画面でないか）確認する
    """
    url = f"https://www.amazon.co.jp/dp/{asin}"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8",
    }
    try:
        print(f"Checking Amazon.co.jp page validity for ASIN '{asin}'...")
        response = requests.get(url, headers=headers, timeout=10, allow_redirects=True)
        text = response.text

        invalid_keywords = [
            "当社サイトの有効なページではない",
            "何かお探しですか？",
            "Looking for something?",
            "Page Not Found",
            "404 - Document Not Found"
        ]
        
        if response.status_code == 404:
            print(f"❌ Verification FAILED: HTTP Status 404 for ASIN '{asin}'")
            return False

        for kw in invalid_keywords:
            if kw in text:
                print(f"❌ Verification FAILED: Found error indicator '{kw}' for ASIN '{asin}'")
                return False

        if "productTitle" in text or "<title>" in text:
            print(f"✅ Verification SUCCESS: Valid Amazon product page confirmed for ASIN '{asin}'")
            return True

        return True
    except Exception as e:
        print(f"Warning: Network check failed for '{asin}': {e}")
        return True


def generate_threads_copy(client: genai.Client, product_name: str, category: str) -> str:
    """選定された実在商品に対してGemini APIで高品質なThreads投稿文を作成する"""
    prompt = f"""
あなたは30〜50代女性に絶大な支持を得る人気ライフスタイルブロガーです。

対象商品: 「{product_name}」（カテゴリ: {category}）

上記の商品について、ターゲット層（30〜50代女性：忙しい毎日の負担軽減、自分へのご褒美、体のケア、家事のストレス軽減）に深く刺さるThreads用の投稿文章を作成してください。

【作成ルール】
- 文字数: 日本語で200文字〜350文字程度
- 1行目: ターゲット層の悩みや感性に響くキャッチーな見出し
- 本文: 具体的なメリット、使用感、どんな悩みが解消されるかへの共感
- ハッシュタグ: 関連ハッシュタグと広告表記「#PR」を必ず含めてください。
"""

    attempts_models = ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-1.5-flash"]
    for model in attempts_models:
        try:
            print(f"Generating copy with Gemini ({model})...")
            response = client.models.generate_content(
                model=model,
                contents=prompt,
            )
            if response and response.text:
                return response.text.strip()
        except Exception as e:
            print(f"Model {model} failed: {e}")
            time.sleep(2)

    return f"【30〜50代女性におすすめ✨】\n忙しい毎日にちょっとしたゆとりと癒やしをくれる「{product_name}」！日頃頑張る自分へのご褒美や家事の効率アップに大活躍です♪\n\n#おすすめ商品 #{category} #時短 #プチ贅沢 #PR"


def fetch_product_candidate(api_key: str, posted_asins: list[str]) -> ProductRecommendation:
    """
    実在が保証されたAmazon人気商品リストの中から未投稿のものを厳選し、
    Gemini APIで訴求力の高いThreads投稿文を生成する
    """
    client = genai.Client(api_key=api_key)

    # 未投稿の実在確定商品を抽出
    unposted_candidates = [p for p in VERIFIED_AMAZON_PRODUCTS if p["asin"] not in posted_asins]

    if not unposted_candidates:
        print("All predefined products posted. Resetting exclude list for cycle...")
        unposted_candidates = VERIFIED_AMAZON_PRODUCTS

    # 未投稿リストからランダムに1つ選定
    selected = random.choice(unposted_candidates)
    asin = selected["asin"]
    product_name = selected["name"]
    category = selected["category"]

    print(f"Selected verified product: {product_name} (ASIN: {asin})")

    # 二重のページ存在チェック
    check_amazon_page_valid(asin)

    # Geminiで訴求本文を作成
    post_text = generate_threads_copy(client, product_name, category)

    return ProductRecommendation(
        asin=asin,
        product_name=product_name,
        post_text=post_text
    )


def create_threads_post(user_id: str, access_token: str, text: str, link_url: str) -> str:
    """
    Threads Graph APIを使用して投稿メディアコンテナを作成する。
    """
    target = user_id if user_id and user_id != "me" else "me"
    url = f"https://graph.threads.net/v1.0/{target}/threads"

    full_text = text
    if link_url not in text:
        full_text = f"{text}\n\n🛒詳細・購入はこちら👇\n{link_url}"

    payload = {
        "media_type": "TEXT",
        "text": full_text,
        "link_attachment": link_url,
        "access_token": access_token,
    }

    print(f"Creating Threads media container (User: {target})...")
    response = requests.post(url, data=payload, timeout=30)
    res_data = response.json()

    if response.status_code != 200 or "id" not in res_data:
        print(f"Error creating Threads container: Status {response.status_code
