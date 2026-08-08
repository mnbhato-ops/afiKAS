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

# 万が一Geminiが実在しないASINを出力し続けた場合の確実な実在商品フォールバックリスト（30〜50代女性向け定番人気商品）
FALLBACK_PRODUCTS = [
    {
        "asin": "B0CD7LKGVP",
        "product_name": "パナソニック ヘアドライヤー ナノケア",
        "post_text": "【毎日のドライヤー時間がサロン級ケアに✨】\n忙しい毎朝の髪のお手入れ、手軽にまとまり髪へ導いてくれる人気ドライヤー！髪の乾燥やパサつきに悩む30〜50代女性に大人気。自分へのご褒美ギフトにもぴったりです♪\n\n#パナソニック #ナノケア #ヘアケア #時短美容 #美容家電 #プチ贅沢 #PR"
    },
    {
        "asin": "B09G37P3P8",
        "product_name": "ティファール 電気ケトル 1.0L",
        "post_text": "【忙しい朝の強い味方☕️秒で沸く時短ケトル】\n家事や仕事で忙しい日々に大活躍！すぐにお湯が湧いて温かいお茶やコーヒーでほっと一息。手入れも簡単でキッチンのインテリアにも馴染む優れもの✨\n\n#ティファール #キッチン家電 #時短家事 #便利グッズ #おうち時間 #PR"
    },
    {
        "asin": "B089QPJYMN",
        "product_name": "Refa(リファ) ファインバブル S シャワーヘッド",
        "post_text": "【バスタイムが極上スパに変わるシャワーヘッド🚿】\nウルトラファインバブルの細かい泡が毛穴の汚れをすっきりOFF。肌や髪の乾燥ケアにも効果的で、毎日のバスタイムが癒やしの時間に変わります✨\n\n#リファ #ReFa #シャワーヘッド #美容 #疲労回復 #プチ贅沢 #PR"
    }
]


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


def check_amazon_asin_exists(asin: str) -> bool:
    """Amazon.co.jp に商品ページが実在するかHTTPリクエストで事前確認する"""
    url = f"https://www.amazon.co.jp/dp/{asin}"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ja-JP,ja;q=0.9",
    }
    try:
        print(f"Verifying existence of ASIN '{asin}' on Amazon.co.jp...")
        response = requests.get(url, headers=headers, timeout=10, allow_redirects=True)
        if response.status_code == 404 or "当社サイトの有効なページではない" in response.text:
            print(f"❌ ASIN verification FAILED for '{asin}': Page not found (404).")
            return False
        print(f"✅ ASIN verification SUCCESS for '{asin}'. Product page exists.")
        return True
    except Exception as e:
        print(f"Warning: Could not verify ASIN '{asin}' due to network check error: {e}")
        return True


def fetch_product_candidate(api_key: str, posted_asins: list[str]) -> ProductRecommendation:
    """
    Gemini APIを使用して30〜50代女性向けのおすすめ商品と投稿本文を生成する。
    Amazon実在チェックを実施し、失敗時は実在保証のフォールバックリストを使用する。
    """
    client = genai.Client(api_key=api_key)
    excluded_str = ", ".join(posted_asins) if posted_asins else "なし"

    prompt = f"""
あなたは30〜50代女性に絶大な支持を得る人気ライフスタイルブロガー・商品キュレーターです。

【目的】
Amazon.co.jpで現在実際に販売されている「30〜50代女性」ターゲットの実在・人気商品を1つ厳選し、Threads投稿用データを作成してください。

【ターゲット層の興味・関心領域】
- 忙しい毎日の負担を減らす「時短家電」「キッチンの便利ツール」
- 30〜50代女性向けの「高品質スキンケア」「美容・ヘアケア用品」
- 日々の「疲労回復」「ヘルスケア」「睡眠改善グッズ」
- 日常を少し豊かにする「プチ贅沢品」「自分へのご褒美アイテム」

【過去に投稿済みの除外ASINリスト】
{excluded_str}
※上記リストに含まれるASINは絶対に除外してください。

【注意点】
- 必ずAmazon.co.jpに実在する正当な10桁英数字ASIN（例: B0XXXXXXXX など）を出力してください。架空の型番は厳禁です。

【Threads投稿文作成ルール】
- 文字数: 日本語で200文字〜400文字程度
- 1行目: ターゲット層の悩みや感性に響くキャッチーな見出し
- 本文: 具体的なメリット、使用感、どんな悩みが解消されるかへの共感
- ハッシュタグ: 商品に関連するハッシュタグに加え、広告表記「#PR」を必ず含めてください。
"""

    attempts_configs = [
        {"name": "gemini-3.6-flash with Grounding", "model": "gemini-3.6-flash", "use_grounding": True},
        {"name": "gemini-3.6-flash Standard Mode", "model": "gemini-3.6-flash", "use_grounding": False},
        {"name": "gemini-3.5-flash Standard Mode", "model": "gemini-3.5-flash", "use_grounding": False},
        {"name": "gemini-1.5-flash Standard Mode", "model": "gemini-1.5-flash", "use_grounding": False},
    ]

    for attempt, attempt_info in enumerate(attempts_configs):
        print(f"Fetching recommendation via Gemini API ({attempt_info['name']}) [Attempt {attempt+1}/{len(attempts_configs)}]...")
        
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

            # 1. ASINフォーマット検証（10桁英数字）
            if not re.match(r"^[A-Z0-9]{10}$", asin):
                print(f"Invalid ASIN format received: '{asin}'. Retrying...")
                time.sleep(2)
                continue

            # 2. 重複チェック
            if asin in posted_asins:
                print(f"ASIN '{asin}' was already posted in past. Retrying for another product...")
                time.sleep(2)
                continue

            # 3. Amazon.co.jp 実在検証（HTTP 404 チェック）
            if not check_amazon_asin_exists(asin):
                print(f"ASIN '{asin}' does not exist on Amazon.co.jp. Retrying for another product...")
                time.sleep(2)
                continue

            res_data.asin = asin
            return res_data

        except Exception as e:
            print(f"Error on attempt {attempt+1} ({attempt_info['name']}): {e}")
            time.sleep(2)

    # 万が一AI生成のASINがすべて存在しなかった場合の安全な実在フォールバック
    print("Warning: Could not fetch a verified ASIN from Gemini. Using guaranteed fallback product...")
    for fallback in FALLBACK_PRODUCTS:
        if fallback["asin"] not in posted_asins:
            return ProductRecommendation(
                asin=fallback["asin"],
                product_name=fallback["product_name"],
                post_text=fallback["post_text"]
            )

    raise ValueError("All candidate products (including fallbacks) have already been posted.")


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
