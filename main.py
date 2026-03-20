from fastapi import FastAPI, Request, Depends
from sqlalchemy.orm import Session
import requests
import os
import uuid
from dotenv import load_dotenv
from datetime import datetime,date
import psutil
from database import SessionLocal, engine
from models import Base, Parent, Child, Control
print("CWD:", os.getcwd())
print("FILE:", __file__)
# =========================
# 初期化
# =========================
Base.metadata.create_all(bind=engine)
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
print("ENV FILE EXISTS:", os.path.exists(".env"))
print("TOKEN RAW:", os.getenv("LINE_ACCESS_TOKEN"))
print("ALL ENV:", dict(os.environ))
LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
print("THIS SERVER FILE:", __file__)
if not LINE_ACCESS_TOKEN:
    print(" LINE_ACCESS_TOKEN が設定されていません")

app = FastAPI()

# =========================
# DB
# =========================
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# =========================
# 状態取得
# =========================
def get_status(child, db):
    control = (
        db.query(Control)
        .filter(Control.child_id == child.id)
        .order_by(Control.id.desc())
        .first()
    )
    if not control:
        target=child.target.split(",") if child.target else []
        target = [t.strip() for t in target]
        return {"blocked": False, "remaining": 0,"target":target}

    used = control.used_time

    if control.running:
        used += (datetime.utcnow() - control.start_time).total_seconds()

    remaining = max(0, control.limit - int(used))
    target=child.target.split(",") if child.target else []
    target = [t.strip() for t in target]
    return {
        "blocked": remaining <= 0,
        "remaining": remaining,
        "target":target
    }
# =========================
# 子供デバイス登録
# =========================
@app.post("/register")
def register_device(db: Session = Depends(get_db)):
    try:
        device_id = str(uuid.uuid4())
        pair_code = str(uuid.uuid4())

        child = Child(
            device_id=device_id,
            pair_code=pair_code,
            default_limit=1800  # デフォルト30分
        )

        db.add(child)
        db.commit()

        return {"device_id": device_id, "pair_code": pair_code}

    except Exception as e:
        print("Register error:", e)
        return {"error": str(e)}

# =========================
# 制限開始
# =========================
@app.post("/start")
def start(device_id: str, db: Session = Depends(get_db)):
    child = db.query(Child).filter(Child.device_id == device_id).first()

    if not child:
        return {"error": "device not found"}

    control = (
        db.query(Control)
        .filter(Control.child_id == child.id)
        .order_by(Control.id.desc())
        .first()
    )
    today=date.today()
    # 初回（Controlなし）
    if Control.dates!=str(today):
        print("dateerror")
    if not control or Control.dates!=str(today):
        control = Control(
            child_id=child.id,
            limit=child.default_limit,
            used_time=0,
            running=True,
            start_time=datetime.utcnow(),
            dates=str(today)
        )
        db.add(control)
        db.commit()
        return {"status": "started"}

    # すでに動いてる
    if control.running:
        return {"status": "already running"}

    # 再開
    control.start_time = datetime.utcnow()
    control.running = True
    db.commit()

    return {"status": "resumed"}
# =========================
# 制限中止
# =========================
@app.post("/pause")
def pause(device_id: str, db: Session = Depends(get_db)):
    child = db.query(Child).filter(Child.device_id == device_id).first()

    if not child:
        return {"error": "device not found"}

    control = (
        db.query(Control)
        .filter(Control.child_id == child.id)
        .order_by(Control.id.desc())
        .first()
    )

    if not control or not control.running:
        return {"status": "not running"}

    elapsed = (datetime.utcnow() - control.start_time).total_seconds()
    control.used_time += int(elapsed)
    control.running = False
    db.commit()

    return {"status": "paused"}
#==========================
#延長申請用API
#==========================
@app.post("/help")
def help_request(device_id: str, minutes: int = 0, db: Session = Depends(get_db)):
    child = db.query(Child).filter(Child.device_id == device_id).first()

    if not child or not child.parent_id:
        return {"error": "not found"}

    parent = db.query(Parent).filter(Parent.id == child.parent_id).first()

    if minutes > 0:
        msg = f"{minutes}分延長してほしい！"
    else:
        msg = "延長してほしい！"

    send_line_push(parent.line_user_id, msg)

    return {"status": "sent"}
# =========================
# 状態取得API
# =========================
@app.get("/status/{device_id}")
def status(device_id: str, db: Session = Depends(get_db)):
    try:
        child = db.query(Child).filter(Child.device_id == device_id).first()

        if not child:
            return {"blocked": False, "remaining": 0}
        print("DB TARGET RAW:", child.target)
        data=get_status(child, db)
        print(data)
        return data

    except Exception as e:
        print("Status error:", e)
        return {"blocked": False, "remaining": 0}

# =========================
# LINE返信
# =========================
def send_line_push(user_id, text):
    try:
        res = requests.post(
            "https://api.line.me/v2/bot/message/push",
            headers={
                "Authorization": f"Bearer {LINE_ACCESS_TOKEN}",
                "Content-Type": "application/json"
            },
            json={
                "to": user_id,
                "messages": [{"type": "text", "text": text}]
            }
        )
        print("PUSH:", res.status_code, res.text)
    except Exception as e:
        print("Push error:", e)

# =========================
# Webhook
# =========================
@app.post("/callback")
async def callback(request: Request, db: Session = Depends(get_db)):
    try:
        body = await request.json()

        for event in body.get("events", []):
            if event["type"] != "message":
                continue

            user_id = event["source"]["userId"]
            text = event["message"]["text"].strip()
            reply_token = event["replyToken"]

            # =====================
            # ペアリング
            # =====================
            if text.startswith("pair"):
                parts = text.split(maxsplit=1)

                if len(parts) != 2:
                    send_line_push(user_id, "使い方: pair コード")
                    continue

                code = parts[1]

                child = db.query(Child).filter(
                    Child.pair_code == code
                ).first()

                if not child:
                    send_line_push(user_id, "コード無効")
                    continue

                parent = db.query(Parent).filter(
                    Parent.line_user_id == user_id
                ).first()

                if not parent:
                    parent = Parent(line_user_id=user_id)
                    db.add(parent)
                    db.commit()

                child.parent_id = parent.id
                db.commit()

                send_line_push(user_id, "ペアリング成功")
                continue

            # =====================
            # 親取得
            # =====================
            parent = db.query(Parent).filter(
                Parent.line_user_id == user_id
            ).first()

            if not parent:
                send_line_push(user_id, "未登録です")
                continue

            child = db.query(Child).filter(
                Child.parent_id == parent.id
            ).first()

            if not child:
                send_line_push(user_id, "子供未登録")
                continue
            # =====================
            # 制限取得
            # =====================
            control = (
                db.query(Control)
                .filter(Control.child_id == child.id)
                .order_by(Control.id.desc())
                .first()
            )

            # =====================
            # limit設定
            # =====================
            if text.startswith("limit"):
                parts = text.split(maxsplit=1)

                if len(parts) != 2 or not parts[1].isdigit():
                    send_line_push(user_id, "使い方: limit 秒")
                    continue

                sec = int(parts[1])
                child.default_limit = sec
                db.commit()

                send_line_push(user_id, f"制限時間を{sec}秒に設定")
            # =====================
            # target設定
            # =====================
            elif text.startswith("target"):
                parts = text.split(maxsplit=1)
                if len(parts) != 2 :
                    send_line_push(user_id, "使い方: target アプリ名")
                    continue
                appname=parts[1].strip()
                child.target=appname
                child.target = parts[1].strip()
                db.commit()
                send_line_push(user_id,f"対象アプリを {appname} に設定しました")
                print("AFTER SAVE:", child.target)
            # =====================
            # 延長
            # =====================
            elif text.startswith("extend"):
                parts = text.split(maxsplit=1)
                if len(parts) != 2 or not parts[1].isdigit():
                    send_line_push(user_id, "使い方: extend 秒")
                    continue
                used = control.used_time

                if control.running:
                    used += (datetime.utcnow() - control.start_time).total_seconds()
                sec = int(parts[1])
                control.limit=max(control.limit+sec,used+sec)
                db.commit()

                send_line_push(user_id, f"制限時間を{sec}秒延長")
            # =====================
            # status
            # =====================
            elif text == "status":
                data = get_status(child, db)
                send_line_push(user_id, f"残り:{data['remaining']}秒")

            else:
                send_line_push(user_id, "コマンド: pair / limit / status")

    except Exception as e:
        print("Webhook error:", e)

    return {"status": "ok"}
