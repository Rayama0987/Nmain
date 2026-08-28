import os
import json
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, Request, Response, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import stripe

# --------------------------------------------------
# 1. Stripe設定（自身のキー・IDに差し替えてください）
# --------------------------------------------------
stripe.api_key = "sk_test_51U8HkND8y79msBAZoDfIIYZ4dVmYWZ6yIdS48zx0mAhZythsYnhrsU6U9yeHqEGm8C8uVZml5rGiE1qx2GVICHu200FquNDH8x"  # Stripeのシークレットキー
STRIPE_WEBHOOK_SECRET = "whsec_VXQI9PQuSeeHIdR3cVKIhIxYocayLexv"  # 後で設定します
STRIPE_PRICE_ID = "price_1U8WNHD8y79msBAZNiUHWjbO"  # Stripeの定期課金Price ID    # Stripe Webhookの署名シークレット

FRONTEND_URL = "https://liteforge3dnet.pages.dev"
DB_FILE = Path("users_db.json")

# --------------------------------------------------
# 2. 簡易データベース初期化
# --------------------------------------------------
def load_db() -> dict:
    if DB_FILE.exists():
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"users": {}}

def save_db(data: dict):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# --------------------------------------------------
# 3. アプリケーション & CORS設定
# --------------------------------------------------
app = FastAPI(title="LiteForge 3D API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://liteforge3dnet.pages.dev",
        "http://localhost:5173",
        "http://localhost:3000",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# --- プリフライト(OPTIONS 503)強制回避ハンドラー ---
@app.options("/{full_path:path}")
async def preflight_handler(full_path: str, request: Request):
    origin = request.headers.get("origin", "https://liteforge3dnet.pages.dev")
    req_headers = request.headers.get("access-control-request-headers", "*")
    response = Response(status_code=200)
    response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS, HEAD, PATCH"
    response.headers["Access-Control-Allow-Headers"] = req_headers
    response.headers["Access-Control-Allow-Credentials"] = "true"
    return response

# --------------------------------------------------
# 4. ヘルスチェック
# --------------------------------------------------
@app.get("/")
async def root():
    return {"status": "ok", "message": "LiteForge 3D Backend is active"}

# --------------------------------------------------
# 5. ログイン・認証API（フロントエンドの各種パスに対応）
# --------------------------------------------------
class LoginRequest(BaseModel):
    username: Optional[str] = None
    email: Optional[str] = None
    password: Optional[str] = None

@app.api_route("/api/login", methods=["GET", "POST"])
@app.api_route("/login", methods=["GET", "POST"])
@app.api_route("/api/auth/login", methods=["GET", "POST"])
@app.api_route("/auth/login", methods=["GET", "POST"])
@app.api_route("/api/token", methods=["GET", "POST"])
async def login_handler(req: Optional[LoginRequest] = None):
    uname = (req.username if req and req.username else None) or "Hannya"
    mail = (req.email if req and req.email else None) or "user@example.com"
    
    db = load_db()
    if mail not in db["users"]:
        db["users"][mail] = {
            "username": uname,
            "email": mail,
            "plan": "free",
            "subscription_id": None
        }
        save_db(db)
    
    user_info = db["users"][mail]
    
    return {
        "status": "success",
        "access_token": "liteforge_token_active",
        "token_type": "bearer",
        "user": user_info
    }

# --------------------------------------------------
# 6. Stripe Checkout セッション作成 API
# --------------------------------------------------
class CheckoutRequest(BaseModel):
    user_email: Optional[str] = "user@example.com"

@app.api_route("/api/create-checkout-session", methods=["POST", "GET"])
@app.api_route("/create-checkout-session", methods=["POST", "GET"])
async def create_checkout_session(req: Optional[CheckoutRequest] = None):
    try:
        email = req.user_email if req and req.user_email else "user@example.com"
        
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            mode="subscription",
            line_items=[{
                "price": STRIPE_PRICE_ID,
                "quantity": 1,
            }],
            customer_email=email,
            client_reference_id=email,
            success_url=f"{FRONTEND_URL}/?status=success",
            cancel_url=f"{FRONTEND_URL}/?status=cancel",
        )
        return {"url": session.url}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --------------------------------------------------
# 7. Stripe Webhook（加入・解約のリアルタイム同期）
# --------------------------------------------------
@app.post("/webhook")
@app.post("/api/webhook")
async def stripe_webhook(request: Request, stripe_signature: Optional[str] = Header(None)):
    payload = await request.body()
    event = None

    try:
        if STRIPE_WEBHOOK_SECRET and stripe_signature:
            event = stripe.Webhook.construct_event(
                payload, stripe_signature, STRIPE_WEBHOOK_SECRET
            )
        else:
            event = json.loads(payload)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Webhook verification failed: {str(e)}")

    event_type = event.get("type")
    data_object = event.get("data", {}).get("object", {})

    db = load_db()

    # ① 決済完了（Proプランへの昇格）
    if event_type == "checkout.session.completed":
        email = data_object.get("customer_email") or data_object.get("client_reference_id")
        sub_id = data_object.get("subscription")
        
        if email:
            if email not in db["users"]:
                db["users"][email] = {"username": email.split("@")[0], "email": email}
            
            db["users"][email]["plan"] = "pro"
            db["users"][email]["subscription_id"] = sub_id
            save_db(db)
            print(f"[PRO昇格] ユーザー: {email} がProプランに加入しました。")

    # ② サブスク解約（Freeプランへの降格）
    elif event_type == "customer.subscription.deleted":
        sub_id = data_object.get("id")
        for email, udata in db["users"].items():
            if udata.get("subscription_id") == sub_id:
                udata["plan"] = "free"
                udata["subscription_id"] = None
                save_db(db)
                print(f"[FREE降格] ユーザー: {email} のサブスクが解約されました。")
                break

    return {"status": "success"}

# --------------------------------------------------
# 8. サーバー起動
# --------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=9000, reload=True)