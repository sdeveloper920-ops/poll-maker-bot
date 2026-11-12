import os, time, json, sqlite3, threading, secrets
from datetime import datetime, timezone
from functools import wraps
import requests
from flask import Flask, request, redirect, url_for, session, render_template, flash

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "CHANGE_THIS_PASSWORD")
SECRET_KEY = os.getenv("SECRET_KEY") or secrets.token_hex(32)
DB_PATH = os.getenv("DB_PATH", "polls.db")
API = f"https://api.telegram.org/bot{BOT_TOKEN}"
app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")
drafts = {}
draft_lock = threading.Lock()

def db():
    c = sqlite3.connect(DB_PATH, timeout=20)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS users(
            user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
            blocked INTEGER DEFAULT 0, joined_at TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS polls(
            id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER,
            question TEXT NOT NULL, options TEXT NOT NULL, chat_id TEXT NOT NULL,
            poll_type TEXT NOT NULL, anonymous INTEGER NOT NULL,
            multiple_answers INTEGER NOT NULL, correct_option INTEGER,
            explanation TEXT, telegram_message_id INTEGER, telegram_poll_id TEXT,
            status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL)""")

def tg(method, payload=None):
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN সেট করা হয়নি।")
    r = requests.post(f"{API}/{method}", json=payload or {}, timeout=40)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(data.get("description", "Telegram API error"))
    return data["result"]

def send(chat_id, text, keyboard=None):
    p = {"chat_id": chat_id, "text": text}
    if keyboard:
        p["reply_markup"] = {"inline_keyboard": keyboard}
    return tg("sendMessage", p)

def user_upsert(u):
    with db() as c:
        c.execute("""INSERT INTO users(user_id,username,first_name,joined_at)
            VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
            username=excluded.username, first_name=excluded.first_name""",
            (u["id"], u.get("username"), u.get("first_name",""),
             datetime.now(timezone.utc).isoformat(timespec="seconds")))

def save_poll(owner, d):
    payload = {
        "chat_id": d["target"], "question": d["question"], "options": d["options"],
        "type": d["type"], "is_anonymous": d["anonymous"],
        "allows_multiple_answers": d["multiple"] if d["type"] == "regular" else False
    }
    if d["type"] == "quiz":
        payload["correct_option_id"] = d["correct"]
        if d.get("explanation"):
            payload["explanation"] = d["explanation"][:200]
    result = tg("sendPoll", payload)
    with db() as c:
        c.execute("""INSERT INTO polls(owner_id,question,options,chat_id,poll_type,anonymous,
            multiple_answers,correct_option,explanation,telegram_message_id,telegram_poll_id,status,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (owner, d["question"], json.dumps(d["options"], ensure_ascii=False), d["target"],
             d["type"], int(d["anonymous"]), int(d["multiple"]), d.get("correct"),
             d.get("explanation",""), result["message_id"], result["poll"]["id"], "published",
             datetime.now(timezone.utc).isoformat(timespec="seconds")))

def bot_update(update):
    if "callback_query" in update:
        q = update["callback_query"]
        uid = q["from"]["id"]
        chat_id = q["message"]["chat"]["id"]
        data = q.get("data", "")
        tg("answerCallbackQuery", {"callback_query_id": q["id"]})
        with db() as c:
            user = c.execute("SELECT blocked FROM users WHERE user_id=?", (uid,)).fetchone()
        if user and user["blocked"]:
            send(chat_id, "এই বট ব্যবহারের অনুমতি তোমার নেই।")
            return
        if data == "newpoll":
            with draft_lock:
                drafts[uid] = {"step":"question","type":"regular","anonymous":True,"multiple":False}
            send(chat_id, "📝 Poll-এর প্রশ্নটি পাঠাও।\\nবন্ধ করতে /cancel লিখো।")
        elif data == "type_regular" or data == "type_quiz":
            with draft_lock:
                d = drafts.get(uid)
                if not d: return
                d["type"] = "quiz" if data == "type_quiz" else "regular"
                d["step"] = "options"
            send(chat_id, "প্রতিটি লাইনে একটি করে ২–১০টি অপশন পাঠাও।")
        elif data in ("anon_yes","anon_no"):
            with draft_lock:
                d = drafts.get(uid)
                if not d: return
                d["anonymous"] = data == "anon_yes"
                d["step"] = "multiple" if d["type"] == "regular" else "correct"
            if d["type"] == "regular":
                send(chat_id, "একাধিক উত্তর Allow করবে?", [[{"text":"হ্যাঁ","callback_data":"multi_yes"},{"text":"না","callback_data":"multi_no"}]])
            else:
                send(chat_id, "সঠিক অপশনের নম্বর পাঠাও (১ থেকে শুরু)।")
        elif data in ("multi_yes","multi_no"):
            with draft_lock:
                d = drafts.get(uid)
                if not d: return
                d["multiple"] = data == "multi_yes"
                d["step"] = "target"
            send(chat_id, "কোন Group/Channel-এ Poll পাঠাবে? @username অথবা numeric Chat ID পাঠাও।")
        elif data == "publish_confirm":
            with draft_lock:
                d = drafts.get(uid)
            if not d:
                send(chat_id, "Draft পাওয়া যায়নি। /start দিয়ে আবার শুরু করো।")
                return
            try:
                save_poll(uid, d)
                with draft_lock: drafts.pop(uid, None)
                send(chat_id, "✅ Poll সফলভাবে Publish হয়েছে!")
            except Exception as e:
                send(chat_id, "Poll পাঠানো যায়নি: " + str(e))
        elif data == "cancel_draft":
            with draft_lock: drafts.pop(uid, None)
            send(chat_id, "Draft বাতিল করা হয়েছে। /start দিয়ে আবার শুরু করো।")
        return

    m = update.get("message", {})
    u = m.get("from", {})
    uid = u.get("id")
    chat_id = m.get("chat", {}).get("id")
    txt = m.get("text", "")
    if not uid or not chat_id:
        return
    user_upsert(u)
    with db() as c:
        row = c.execute("SELECT blocked FROM users WHERE user_id=?", (uid,)).fetchone()
    if row and row["blocked"]:
        send(chat_id, "এই বট ব্যবহারের অনুমতি তোমার নেই।")
        return
    if txt.startswith("/start") or txt.startswith("/help"):
        send(chat_id, "👋 Welcome to Poll Maker Bot Premium!\\n\\nনিচের বাটন থেকে Telegram-এই Poll তৈরি করো।",
             [[{"text":"➕ Create Poll","callback_data":"newpoll"}]])
        return
    if txt.startswith("/cancel"):
        with draft_lock: drafts.pop(uid, None)
        send(chat_id, "Draft বাতিল হয়েছে।")
        return
    with draft_lock:
        d = drafts.get(uid)
        if not d:
            return
        step = d.get("step")
        if step == "question":
            if not txt or len(txt) > 300:
                send(chat_id, "প্রশ্ন ১–৩০০ অক্ষরের মধ্যে পাঠাও।")
                return
            d["question"] = txt
            d["step"] = "type"
            send(chat_id, "Poll Type নির্বাচন করো:", [[
                {"text":"Regular Poll","callback_data":"type_regular"},
                {"text":"Quiz Poll","callback_data":"type_quiz"}]])
        elif step == "options":
            opts = [x.strip() for x in txt.splitlines() if x.strip()]
            if len(opts) < 2 or len(opts) > 10 or any(len(x)>100 for x in opts):
                send(chat_id, "২–১০টি অপশন দাও। প্রতিটি সর্বোচ্চ ১০০ অক্ষর।")
                return
            d["options"] = opts
            d["step"] = "anonymous"
            send(chat_id, "ভোট Anonymous হবে?", [[
                {"text":"হ্যাঁ","callback_data":"anon_yes"},
                {"text":"না","callback_data":"anon_no"}]])
        elif step == "correct":
            try:
                n = int(txt) - 1
                if n < 0 or n >= len(d["options"]): raise ValueError()
                d["correct"] = n
                d["step"] = "explanation"
                send(chat_id, "Quiz explanation পাঠাও, না থাকলে - লিখো।")
            except ValueError:
                send(chat_id, f"১ থেকে {len(d['options'])} এর মধ্যে সঠিক অপশনের নম্বর পাঠাও।")
        elif step == "explanation":
            d["explanation"] = "" if txt == "-" else txt[:200]
            d["step"] = "target"
            send(chat_id, "কোন Group/Channel-এ Poll পাঠাবে? @username অথবা numeric Chat ID পাঠাও।")
        elif step == "target":
            d["target"] = txt.strip()
            d["step"] = "confirm"
            summary = "📋 Poll Preview\\n\\n" + d["question"] + "\\n" + "\\n".join(
                f"{i+1}. {x}" for i,x in enumerate(d["options"]))
            summary += "\\n\\nType: " + d["type"].upper() + "\\nTarget: " + d["target"]
            send(chat_id, summary, [[
                {"text":"🚀 Publish","callback_data":"publish_confirm"},
                {"text":"Cancel","callback_data":"cancel_draft"}]])
        else:
            send(chat_id, "Preview-এর Publish বা Cancel বাটন ব্যবহার করো।")

def poll_loop():
    if not BOT_TOKEN:
        print("BOT_TOKEN missing; bot polling disabled.")
        return
    offset = 0
    while True:
        try:
            updates = tg("getUpdates", {"offset":offset,"timeout":25,
                                        "allowed_updates":["message","callback_query"]})
            for u in updates:
                offset = u["update_id"] + 1
                try: bot_update(u)
                except Exception as e: print("Update error:", e)
        except Exception as e:
            print("Polling error:", e)
            time.sleep(5)

def login_required(fn):
    @wraps(fn)
    def wrapped(*a, **kw):
        if not session.get("admin"): return redirect(url_for("login"))
        return fn(*a, **kw)
    return wrapped

@app.route("/")
def home():
    return redirect(url_for("dashboard") if session.get("admin") else url_for("login"))

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        if request.form.get("username") == ADMIN_USERNAME and request.form.get("password") == ADMIN_PASSWORD:
            session["admin"] = True
            return redirect(url_for("dashboard"))
        flash("Username অথবা Password সঠিক নয়।")
    return render_template("admin.html", page="login")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/dashboard", methods=["GET","POST"])
@login_required
def dashboard():
    if request.method == "POST":
        q = request.form.get("question","").strip()
        options = [x.strip() for x in request.form.get("options","").splitlines() if x.strip()]
        target = request.form.get("chat_id","").strip()
        ptype = request.form.get("poll_type","regular")
        anonymous = request.form.get("anonymous") == "on"
        multiple = request.form.get("multiple") == "on"
        correct = request.form.get("correct_option","")
        if not q or len(q)>300: flash("প্রশ্ন ১–৩০০ অক্ষরের মধ্যে হতে হবে।")
        elif len(options)<2 or len(options)>10: flash("২–১০টি অপশন দিতে হবে।")
        elif not target: flash("Target Group/Channel ID দিন।")
        elif ptype=="quiz" and (not correct.isdigit() or int(correct)<0 or int(correct)>=len(options)):
            flash("Quiz-এর সঠিক অপশন নম্বর দিন (০ থেকে শুরু)।")
        else:
            d={"question":q,"options":options,"target":target,"type":ptype,
               "anonymous":anonymous,"multiple":multiple,"correct":int(correct) if ptype=="quiz" else None}
            try:
                result=tg("sendPoll", {"chat_id":target,"question":q,"options":options,
                    "type":ptype,"is_anonymous":anonymous,
                    "allows_multiple_answers":multiple if ptype=="regular" else False,
                    **({"correct_option_id":int(correct)} if ptype=="quiz" else {})})
                with db() as c:
                    c.execute("""INSERT INTO polls(owner_id,question,options,chat_id,poll_type,anonymous,
                        multiple_answers,correct_option,telegram_message_id,telegram_poll_id,status,created_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (0,q,json.dumps(options,ensure_ascii=False),target,ptype,int(anonymous),int(multiple),
                         d["correct"],result["message_id"],result["poll"]["id"],"published",
                         datetime.now(timezone.utc).isoformat(timespec="seconds")))
                flash("✅ Admin থেকে Poll Publish হয়েছে!")
            except Exception as e: flash("Poll পাঠানো যায়নি: "+str(e))
    with db() as c:
        total=c.execute("SELECT COUNT(*) FROM polls").fetchone()[0]
        users=c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        published=c.execute("SELECT COUNT(*) FROM polls WHERE status='published'").fetchone()[0]
        polls=c.execute("SELECT * FROM polls ORDER BY id DESC LIMIT 40").fetchall()
    return render_template("admin.html",page="dashboard",total=total,users=users,published=published,polls=polls)

@app.route("/users")
@login_required
def users_page():
    with db() as c: users=c.execute("SELECT * FROM users ORDER BY joined_at DESC LIMIT 300").fetchall()
    return render_template("admin.html",page="users",users=users)

@app.post("/user/<int:user_id>/toggle")
@login_required
def toggle_user(user_id):
    with db() as c:
        c.execute("UPDATE users SET blocked=CASE WHEN blocked=1 THEN 0 ELSE 1 END WHERE user_id=?",(user_id,))
    flash("User status আপডেট হয়েছে।")
    return redirect(url_for("users_page"))

@app.route("/broadcast",methods=["GET","POST"])
@login_required
def broadcast():
    if request.method=="POST":
        message=request.form.get("message","").strip()
        if not message: flash("Broadcast message লিখুন।")
        else:
            with db() as c: ids=[r["user_id"] for r in c.execute("SELECT user_id FROM users WHERE blocked=0").fetchall()]
            ok=0
            for uid in ids:
                try: tg("sendMessage",{"chat_id":uid,"text":message}); ok+=1
                except Exception: pass
                time.sleep(0.04)
            flash(f"Broadcast সম্পন্ন: {ok}/{len(ids)} জনকে পাঠানো হয়েছে।")
    return render_template("admin.html",page="broadcast")

@app.post("/poll/<int:pid>/close")
@login_required
def close_poll(pid):
    with db() as c: row=c.execute("SELECT * FROM polls WHERE id=?",(pid,)).fetchone()
    if row and row["status"]=="published":
        try:
            tg("stopPoll",{"chat_id":row["chat_id"],"message_id":row["telegram_message_id"]})
            with db() as c: c.execute("UPDATE polls SET status='closed' WHERE id=?",(pid,))
            flash("Poll বন্ধ করা হয়েছে।")
        except Exception as e: flash("Poll বন্ধ করা যায়নি: "+str(e))
    else: flash("Poll পাওয়া যায়নি অথবা আগে থেকেই বন্ধ।")
    return redirect(url_for("dashboard"))

@app.route("/health")
def health(): return {"status":"ok"}

if __name__=="__main__":
    init_db()
    threading.Thread(target=poll_loop,daemon=True).start()
    app.run(host="0.0.0.0",port=int(os.getenv("PORT","10000")))
