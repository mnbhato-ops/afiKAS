import sys
import os
import json
import time
import requests
import re
import urllib.parse

print("=== SNS Auto Publisher Started ===", flush=True)

try:
    from playwright.sync_api import sync_playwright
    import requests_oauthlib
    print("ライブラリ読み込み完了", flush=True)
except Exception as e:
    print(f"ライブラリ読み込みエラー: {e}", flush=True)
    sys.exit(1)

# --- 環境変数の読み込み ---
GEMINI_KEY = os.environ.get("GEMINI_KEY")
NOTE_SESSION = os.environ.get("NOTE_SESSION")
AMAZON_ID = os.environ.get("AMAZON_ID", "")

X_CONSUMER_KEY = os.environ.get("X_CONSUMER_KEY")
X_CONSUMER_SECRET = os.environ.get("X_CONSUMER_SECRET")
X_USER_TOKEN = os.environ.get("X_USER_TOKEN")
X_USER_SECRET = os.environ.get("X_USER_SECRET")

HISTORY_LOG = "history.log"

if not all([GEMINI_KEY, NOTE_SESSION]):
    print("エラー: 必須の環境変数が設定されていません。", flush=True)
    sys.exit(1)

# 過去の紹介履歴（ASIN）を読み込む
def get_history():
    if os.path.exists(HISTORY_LOG):
        with open(HISTORY_LOG, "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]
    return []

# 新しい商品（ASIN）を履歴に追加
def add_history(item_asin):
    with open(HISTORY_LOG, "a", encoding="utf-8") as f:
        f.write(f"{item_asin}\n")

# 1. 指定されたAmazonのランキングページから売れ筋商品を1つスクレイピング
def fetch_real_amazon_item(history):
    target_url = "https://www.amazon.co.jp/b?ref=SiteStripe&node=24999964051"
    print(f"1/4 Amazonランキングから商品を直接取得中... ({target_url})", flush=True)
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        
        try:
            page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)
            
            links = page.locator("a[href*='/dp/'], a[href*='/product/']").all()
            
            for link in links:
                href = link.get_attribute("href") or ""
                match = re.search(r'/(?:dp|product)/([A-Z0-9]{10})', href)
                if match:
                    asin = match.group(1)
                    if asin in history:
                        continue
                        
                    title = link.text_content().strip()
                    if not title:
                        img = link.locator("img").first
                        if img.count() > 0:
                            title = img.get_attribute("alt") or ""
                    
                    if title and len(title) > 5:
                        print(f" -> 取得成功: 【{title[:30]}...】 (ASIN: {asin})", flush=True)
                        browser.close()
                        return title, asin
                        
        except Exception as e:
            print(f" -> Amazonアクセス中にエラーが発生しました: {e}", flush=True)
            
        browser.close()
        
    print(" -> 商品取得に失敗したため、デフォルト商品を使用します。", flush=True)
    return "Amazon Echo Dot (エコードット)", "B09B8XJ7X3"

# 2. 記事＆告知文の生成
def build_content(item_name, item_asin):
    print(f"2/4 【{item_name[:20]}...】のコンテンツを生成中...", flush=True)

    amazon_url = f"https://www.amazon.co.jp/dp/{item_asin}?tag={AMAZON_ID}" if AMAZON_ID else f"https://www.amazon.co.jp/dp/{item_asin}"

    prompt = f"""
    Amazonの売れ筋商品「{item_name}」を紹介する、洗練されたガジェット系note記事とX(Twitter)告知文を作成してください。
    指定するnoteクリエイター（yohaku_gadget風）の、読みやすく美しいスタイルを完全に再現してください。
    
    【スタイル・ルールの絶対遵守】
    ・文体：「〜です・〜ます」調。読者に語りかけるような親しみやすさと、説得力のあるレビュー。
    ・空白と余白：スマホでの読みやすさを最優先し、1〜2文ごとに必ず改行（空白行）を入れること。文字が詰まった長文段落は絶対にNGです。
    ・見出し：大見出しは必ず行頭に「## 」「### 」を使用してください。
    
    【記事の構成（この通りに書いてください）】
    [1行目: 惹きつけるタイトル]
    [2行目以降: 本文]
    
    （導入文）
    
    ## 結論：一言でいうとどんな商品？
    （結論）
    
    ## 〇〇のここがスゴイ
    （メリット解説）
    
    ## 気になる点・注意点
    （デメリット解説）
    
    ## こんな人におすすめ
    （リスト）
    
    ## まとめ
    （総評）
    
    👇 Amazonで詳細やレビューを確認する
    [[AMAZON_URL]]
    
    ※この記事にはAmazonアソシエイトリンクが含まれています
    
    ---X_POST---
    [X(Twitter)用の告知文（100文字程度・絵文字付き・記事への誘導・ハッシュタグ付き）]
    """
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent?key={GEMINI_KEY}"
    headers = {"Content-Type": "application/json"}
    payload = {"contents": [{"parts": [{"text": prompt}]}]}

    for attempt in range(3):
        response = requests.post(url, json=payload, headers=headers)
        if response.status_code == 200:
            break
        elif response.status_code == 429:
            print(f" -> リクエスト制限のため2分間待機中... ({attempt + 1}/3)", flush=True)
            time.sleep(120)

    if response.status_code != 200:
        raise Exception(f"Gemini API Error: {response.status_code}")

    res_json = response.json()
    full_text = res_json['candidates'][0]['content']['parts'][0]['text'].strip()
    
    if "---X_POST---" in full_text:
        note_part, x_text = full_text.split("---X_POST---", 1)
    else:
        note_part, x_text = full_text, f"本日のおすすめ商品レビューを公開しました！✨"

    lines = note_part.strip().split("\n")
    title = lines[0].replace("# ", "").replace("## ", "").strip()
    body = "\n".join(lines[1:]).strip()
    
    body = body.replace("[[AMAZON_URL]]", amazon_url)
    while "\n\n\n" in body:
        body = body.replace("\n\n\n", "\n\n")
    
    return title, body, x_text.strip(), amazon_url

# 3. noteへ投稿（確実なペーストによるカード化＆投稿ボタン完全クリック）
def publish_note(title, body, amazon_url):
    print("3/4 noteへ自動投稿中（リッチテキスト変換処理）...", flush=True)
    
    state_data = json.loads(NOTE_SESSION)
    with open("session_temp.json", "w") as f:
        json.dump(state_data, f)

    post_url = ""

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # クリップボードアクセス権限を許可
        context = browser.new_context(
            storage_state="session_temp.json",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            permissions=["clipboard-read", "clipboard-write"]
        )
        page = context.new_page()
        
        print(" -> エディタを開いています...", flush=True)
        page.goto("https://note.com/notes/new", wait_until="networkidle")
        
        title_selector = "textarea[placeholder*='タイトル'], textarea[placeholder*='記事タイトル'], textarea"
        page.wait_for_selector(title_selector, timeout=30000)
        page.fill(title_selector, title)
        page.wait_for_timeout(1000)
        
        body_selector = "div[data-placeholder*='本文'], div[contenteditable='true']"
        page.wait_for_selector(body_selector, timeout=15000)
        page.click(body_selector)
        page.wait_for_timeout(500)

        paragraphs = body.split("\n")
        for line in paragraphs:
            trimmed_line = line.strip()
            
            if not trimmed_line:
                page.keyboard.press("Enter")
                continue
            
            # URLが含まれる行の判定（部分一致で確実に捕獲）
            if amazon_url in trimmed_line or ("amazon.co.jp" in trimmed_line and "http" in trimmed_line):
                print(" -> URLのリンクカード化処理（Paste動作）を実行中...", flush=True)
                
                # クリップボード経由でURLを貼り付け（ProseMirrorが埋め込みカードへ変換）
                page.evaluate(f"navigator.clipboard.writeText('{amazon_url}')")
                page.keyboard.press("Control+v")
                page.wait_for_timeout(1000)
                page.keyboard.press("Enter")
                print(" -> リンクカード読み込み完了待機中...", flush=True)
                page.wait_for_timeout(6000)
                page.keyboard.press("Enter")
                
            elif trimmed_line.startswith("### "):
                page.keyboard.type("### ")
                page.wait_for_timeout(300) 
                page.keyboard.insert_text(trimmed_line[4:])
                page.keyboard.press("Enter")
                
            elif trimmed_line.startswith("## "):
                page.keyboard.type("## ")
                page.wait_for_timeout(300) 
                page.keyboard.insert_text(trimmed_line[3:])
                page.keyboard.press("Enter")
                
            else:
                page.keyboard.insert_text(trimmed_line)
                page.wait_for_timeout(50)
                page.keyboard.press("Enter")

        page.wait_for_timeout(2000)
        
        # 公開設定ボタンを押す
        print(" -> 公開設定を開きます...", flush=True)
        config_btn = page.locator("button:has-text('公開設定'), button:has-text('公開に進む')").first
        config_btn.wait_for(state="visible", timeout=15000)
        config_btn.click()
        
        # モーダル（公開設定画面）が表示されるまで確実に待つ
        print(" -> 公開画面の準備を待機中...", flush=True)
        page.wait_for_timeout(4000)
        
        print(" -> 投稿を実行しています...", flush=True)
        # モーダル内の投稿ボタンを特定して強固にクリック
        submit_btn = page.locator("button:has-text('投稿する'), button:has-text('公開する'), button:has-text('記事を公開')").last
        submit_btn.wait_for(state="visible", timeout=15000)
        submit_btn.click(force=True)
        
        print(" -> 記事の公開完了を待機中...", flush=True)
        for _ in range(20):
            time.sleep(2)
            current_url = page.url
            if "note.com" in current_url and "editor" not in current_url and "drafts" not in current_url:
                post_url = current_url
                break

        if not post_url:
            post_url = page.url

        print(f" -> 処理終了時URL: {post_url}", flush=True)
        browser.close()
        
        if os.path.exists("session_temp.json"):
            os.remove("session_temp.json")
            
    return post_url

# 4. X(Twitter)へ告知投稿
def publish_x(x_text, post_url):
    print("4/4 X(Twitter)へ送信中...", flush=True)
    if not all([X_CONSUMER_KEY, X_CONSUMER_SECRET, X_USER_TOKEN, X_USER_SECRET]):
        print(" -> X APIキー未設定のためスキップします。", flush=True)
        return

    full_text = f"{x_text}\n\n👇詳細レビューはこちら\n{post_url}"
    if len(full_text) > 280:
        full_text = full_text[:270] + "...\n" + post_url

    endpoint = "https://api.twitter.com/2/tweets"
    auth = requests_oauthlib.OAuth1(X_CONSUMER_KEY, X_CONSUMER_SECRET, X_USER_TOKEN, X_USER_SECRET)
    
    try:
        res = requests.post(endpoint, auth=auth, json={"text": full_text})
        if res.status_code in [200, 201]:
            print(" -> Xへの投稿が完了しました！🎉", flush=True)
        elif res.status_code == 402:
            print(" -> [注意] X APIの制限のため投稿をスキップしました。", flush=True)
        else:
            print(f" -> X投稿失敗: {res.text}", flush=True)
    except Exception as e:
        print(f" -> X投稿エラー: {e}", flush=True)

if __name__ == "__main__":
    history = get_history()
    
    item_title, item_asin = fetch_real_amazon_item(history)
    title, body, x_text, amazon_url = build_content(item_title, item_asin)
    
    print(f"\n生成タイトル: {title}\n", flush=True)
    
    note_url = publish_note(title, body, amazon_url)
    if note_url and "note.com" in note_url and "editor" not in note_url and "drafts" not in note_url:
        print(f"✅ note投稿成功: {note_url}", flush=True)
        publish_x(x_text, note_url)
        add_history(item_asin)
    else:
        print(f"⚠️ noteの完全公開の確認が取れなかったため、履歴保存を保留しました。", flush=True)
