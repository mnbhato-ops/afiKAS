import sys
import os
import json
import time
import requests
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

# 過去の紹介履歴を読み込む
def get_history():
    if os.path.exists(HISTORY_LOG):
        with open(HISTORY_LOG, "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]
    return []

# 新しい商品を履歴に追加
def add_history(item):
    with open(HISTORY_LOG, "a", encoding="utf-8") as f:
        f.write(f"{item}\n")

# 1. 売れ筋ターゲットの選定
def fetch_target_item(history):
    print("1/4 売れ筋商品をリサーチ中...", flush=True)
    
    history_str = "、".join(history[-30:]) if history else "なし"
    
    prompt = f"""
    Amazonやnoteで現在ヒットしている・話題になっている具体的な売れ筋商品（ガジェット、書籍、家電、便利グッズなど）を1つ選定してください。
    
    【ルール】
    ・以下の「紹介済みリスト」にあるものは絶対に除外してください。
    紹介済みリスト: [{history_str}]
    
    ・出力は「選定した商品名（または製品ジャンル名）」のみを1行で返してください。解説や挨拶は不要です。
    """
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent?key={GEMINI_KEY}"
    headers = {"Content-Type": "application/json"}
    payload = {"contents": [{"parts": [{"text": prompt}]}]}

    response = requests.post(url, json=payload, headers=headers)
    if response.status_code == 200:
        item = response.json()['candidates'][0]['content']['parts'][0]['text'].strip()
        print(f" -> 選定アイテム: 【{item}】", flush=True)
        return item
    else:
        print(" -> 選定失敗のためデフォルトテーマを使用します。", flush=True)
        return "最新のおすすめ便利ガジェット"

# 2. 記事＆告知文の生成
def build_content(item_name):
    print(f"2/4 【{item_name}】のコンテンツを生成中...", flush=True)

    safe_item_name = urllib.parse.quote(item_name)
    amazon_url = f"https://www.amazon.co.jp/s?k={safe_item_name}&tag={AMAZON_ID}" if AMAZON_ID else "https://www.amazon.co.jp"

    prompt = f"""
    話題の商品「{item_name}」を紹介するnote記事とX(Twitter)告知文を作成してください。
    
    【ルール】
    ・文体は親しみやすく丁寧な「〜です・〜ます」調
    ・読者の興味を惹く構成で魅力をしっかり解説してください
    
    【出力フォーマット】
    「---X_POST---」という区切り線を必ず挟んで出力してください。

    [1行目: 惹きつけるnoteタイトル]
    [2行目以降: note本文（1200文字程度）]
    ・人気の理由、メリット・デメリット、おすすめな人を詳しく解説。
    ・文章内に「👉 Amazonで詳細やレビューを確認する」というテキストの直後に、以下のURLを単独行として配置してください：
      {amazon_url}
    ・末尾に「※この記事にはAmazonアソシエイトリンクが含まれています」とハッシュタグ3つを記載。

    ---X_POST---
    [X(Twitter)用の告知文（100文字程度・絵文字付き・魅力を簡潔に表現）]
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
        note_part, x_text = full_text, f"本日のおすすめ商品「{item_name}」についてのレビュー記事を公開しました！✨"

    lines = note_part.strip().split("\n")
    title = lines[0].strip()
    body = "\n".join(lines[1:]).strip()
    
    return title, body, x_text.strip(), amazon_url

# 3. noteへ投稿（ペーストイベントのシミュレートによる完全ハイパーリンク化）
def publish_note(title, body, amazon_url):
    print("3/4 noteへ自動投稿中...", flush=True)
    
    state_data = json.loads(NOTE_SESSION)
    with open("session_temp.json", "w") as f:
        json.dump(state_data, f)

    post_url = ""

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            storage_state="session_temp.json",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        page = context.new_page()
        
        print(" -> エディタを開いています...", flush=True)
        page.goto("https://note.com/notes/new", wait_until="networkidle")
        
        # タイトル入力
        title_selector = "textarea[placeholder*='タイトル'], textarea[placeholder*='記事タイトル'], textarea"
        page.wait_for_selector(title_selector, timeout=30000)
        page.fill(title_selector, title)
        page.wait_for_timeout(1000)
        
        # 本文入力エリアの要素を待機
        body_selector = "div[data-placeholder*='本文'], div[contenteditable='true']"
        page.wait_for_selector(body_selector, timeout=15000)
        
        # 本文中の改行を <br> に変換し、Amazon URL 部分を <a> タグに変換
        html_body = body.replace("\n", "<br>")
        if amazon_url in body:
            link_html = f'<a href="{amazon_url}" target="_blank" rel="noopener noreferrer">{amazon_url}</a>'
            html_body = html_body.replace(amazon_url, link_html)

        # クリップボードのペーストイベントを擬似発火させてエディタに注入（ProseMirror対応）
        page.evaluate("""({ selector, htmlContent }) => {
            const el = document.querySelector(selector);
            if (!el) return;
            
            el.focus();
            
            const dataTransfer = new DataTransfer();
            dataTransfer.setData('text/html', htmlContent);
            dataTransfer.setData('text/plain', htmlContent.replace(/<br\s*[\/]?>/gi, "\\n"));
            
            const pasteEvent = new ClipboardEvent('paste', {
                clipboardData: dataTransfer,
                bubbles: true,
                cancelable: true
            });
            
            el.dispatchEvent(pasteEvent);
        }""", {"selector": body_selector, "htmlContent": html_body})
        
        page.wait_for_timeout(3000)
        
        print(" -> 公開設定を開きます...", flush=True)
        config_btn = "button:has-text('公開設定'), button:has-text('公開に進む')"
        page.wait_for_selector(config_btn, timeout=15000)
        page.click(config_btn)
        page.wait_for_timeout(5000)
        
        print(" -> 投稿を実行しています...", flush=True)
        submit_btn = "button:has-text('投稿する'), button:has-text('記事を公開'), button:has-text('公開する'), button:has-text('投稿')"
        page.wait_for_selector(submit_btn, timeout=15000)
        page.click(submit_btn)
        
        print(" -> 記事の公開完了を待機中...", flush=True)
        for _ in range(15):
            time.sleep(2)
            current_url = page.url
            if "note.com" in current_url and "editor.note.com" not in current_url:
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
    auth = requests_oauthlib.OAuth1(
        X_CONSUMER_KEY, X_CONSUMER_SECRET, X_USER_TOKEN, X_USER_SECRET
    )
    
    payload = {"text": full_text}
    
    try:
        res = requests.post(endpoint, auth=auth, json=payload)
        if res.status_code in [200, 201]:
            print(" -> Xへの投稿が完了しました！🎉", flush=True)
        elif res.status_code == 402:
            print(" -> [注意] X APIの制限(Status 402)のため投稿をスキップしました。", flush=True)
        else:
            print(f" -> X投稿失敗 (Status: {res.status_code}): {res.text}", flush=True)
    except Exception as e:
        print(f" -> X投稿エラー: {e}", flush=True)

if __name__ == "__main__":
    history = get_history()
    target_item = fetch_target_item(history)
    
    title, body, x_text, amazon_url = build_content(target_item)
    print(f"\n生成タイトル: {title}\n", flush=True)
    
    note_url = publish_note(title, body, amazon_url)
    if note_url and "note.com" in note_url and "editor.note.com" not in note_url:
        print(f"✅ note投稿成功: {note_url}", flush=True)
        publish_x(x_text, note_url)
        add_history(target_item)
    else:
        print(f"⚠️ noteの完全公開の確認が取れなかったため、URL（{note_url}）でのX投稿および履歴保存を保留しました。", flush=True)
