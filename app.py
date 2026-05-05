import json
import logging
import os
import shutil
import uuid
from datetime import datetime

import portalocker
from flask import Flask, flash, jsonify, redirect, render_template, request, url_for
from flask_wtf.csrf import CSRFProtect

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("app.log", encoding="utf-8"), logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

app.debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"

SECRET_KEY_FILE = ".secret_key"  # nosec: B105
if os.path.exists(SECRET_KEY_FILE):
    with open(SECRET_KEY_FILE, "rb") as f:
        app.secret_key = f.read()
else:
    app.secret_key = os.urandom(32)
    with open(SECRET_KEY_FILE, "wb") as fw:
        fw.write(app.secret_key)

csrf = CSRFProtect(app)

DATA_FILE = "memos.json"
BACKUP_FILE = "memos.json.bak"
LOCK_FILE = "memos.lock"

MAX_TITLE = 200
MAX_CONTENT = 50000


def backup_data():
    if os.path.exists(DATA_FILE):
        shutil.copy2(DATA_FILE, BACKUP_FILE)


def validate_memo_input(title, content):
    if not title or not content:
        return "标题和内容都不能为空！"
    if len(title) > MAX_TITLE:
        return f"标题不能超过{MAX_TITLE}字符！"
    if len(content) > MAX_CONTENT:
        return f"内容不能超过{MAX_CONTENT}字符！"
    return None


def load_memos():
    if not os.path.exists(DATA_FILE):
        if os.path.exists(BACKUP_FILE):
            try:
                with open(BACKUP_FILE, encoding="utf-8") as f:
                    data = json.load(f)
                logger.info("数据文件不存在，从备份恢复")
                return data
            except (OSError, json.JSONDecodeError):
                pass
        return []

    try:
        with open(DATA_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.error(f"数据文件损坏: {e}")
        if os.path.exists(BACKUP_FILE):
            try:
                with open(BACKUP_FILE, encoding="utf-8") as f:
                    data = json.load(f)
                logger.info("已从备份文件恢复数据")
                return data
            except (OSError, json.JSONDecodeError) as be:
                logger.error(f"备份文件也损坏: {be}")
                raise RuntimeError(
                    "数据文件和备份文件均已损坏，无法恢复数据。"
                    "请手动检查 memos.json 和 memos.json.bak"
                ) from be
        raise RuntimeError(
            "数据文件和备份文件均已损坏，无法恢复数据。请手动检查 memos.json 和 memos.json.bak"
        ) from e


def save_memos(memos):
    if not isinstance(memos, list):
        raise TypeError("memos 必须是列表类型")
    backup_data()
    try:
        with (
            portalocker.Lock(LOCK_FILE, "w", timeout=5),
            open(DATA_FILE, "w", encoding="utf-8") as f,
        ):
            json.dump(memos, f, ensure_ascii=False, indent=2)
    except portalocker.LockException as e:
        logger.error(f"保存文件失败: {e}")
        raise


def sorted_memos(memos):
    memos = sorted(memos, key=lambda m: m.get("created_at", ""), reverse=True)
    memos = sorted(memos, key=lambda m: not m.get("pinned", False))
    return memos


def filter_memos(memos, q="", tag=""):
    if q:
        q_lower = q.lower()
        memos = [
            m
            for m in memos
            if q_lower in m.get("title", "").lower() or q_lower in m.get("content", "").lower()
        ]
    if tag:
        memos = [m for m in memos if tag in m.get("tags", [])]
    return memos


def get_all_tags(memos):
    tags = set()
    for m in memos:
        tags.update(m.get("tags", []))
    return sorted(tags)


def build_context(memos, memo_id=None, q="", tag=""):
    memos = sorted_memos(memos)
    total = len(memos)

    # 选中的笔记
    selected = None
    if memo_id:
        selected = next((m for m in memos if str(m.get("id", "")) == str(memo_id)), None)

    # 侧边栏笔记列表：根据搜索/标签条件过滤
    sidebar_memos = filter_memos(memos, q, tag)

    filtered = sidebar_memos

    return {
        "sidebar_memos": sidebar_memos,
        "selected": selected,
        "all_tags": get_all_tags(memos),
        "total": total,
        "filtered_count": len(filtered),
        "search_query": q,
        "current_tag": tag,
    }


@app.route("/")
def index():
    q = request.args.get("q", "").strip()
    tag = request.args.get("tag", "").strip()
    memos = load_memos()
    ctx = build_context(memos, q=q, tag=tag)

    if (q or tag) and ctx["sidebar_memos"] and not ctx["selected"]:
        first_memo = ctx["sidebar_memos"][0]
        return redirect(
            url_for("view_memo", memo_id=first_memo["id"], q=q, tag=tag)
        )

    return render_template("index.html", **ctx)


@app.route("/view/<memo_id>")
def view_memo(memo_id):
    q = request.args.get("q", "").strip()
    tag = request.args.get("tag", "").strip()
    memos = load_memos()
    ctx = build_context(memos, memo_id=memo_id, q=q, tag=tag)

    if not ctx["selected"]:
        flash("备忘录不存在！")
        return redirect(url_for("index"))

    return render_template("index.html", **ctx)


@app.route("/add", methods=["POST"])
def add_memo():
    title = request.form.get("title", "").strip()
    content = request.form.get("content", "").strip()

    error = validate_memo_input(title, content)
    if error:
        flash(error)
        return redirect(url_for("index"))

    tags_raw = request.form.get("tags", "").strip()
    tags = [t.strip() for t in tags_raw.split(",") if t.strip()]

    memos = load_memos()
    new_memo = {
        "id": str(uuid.uuid4()),
        "title": title,
        "content": content,
        "tags": tags,
        "pinned": False,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    memos.insert(0, new_memo)
    save_memos(memos)
    flash("备忘录添加成功！")
    return redirect(url_for("view_memo", memo_id=new_memo["id"]))


@app.route("/edit/<memo_id>", methods=["GET", "POST"])
def edit_memo(memo_id):
    memos = load_memos()
    memo = next((m for m in memos if str(m.get("id", "")) == str(memo_id)), None)

    if not memo:
        flash("备忘录不存在！")
        return redirect(url_for("index"))

    ctx = build_context(memos, memo_id=memo_id)
    ctx["edit_mode"] = True

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        content = request.form.get("content", "").strip()
        tags_raw = request.form.get("tags", "").strip()
        tags = [t.strip() for t in tags_raw.split(",") if t.strip()]

        error = validate_memo_input(title, content)
        if error:
            flash(error)
            ctx["edit_mode"] = True
            return render_template("index.html", **ctx)

        memo["title"] = title
        memo["content"] = content
        memo["tags"] = tags
        memo["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        save_memos(memos)
        flash("备忘录更新成功！")
        return redirect(url_for("view_memo", memo_id=memo_id))

    return render_template("index.html", **ctx)


@app.route("/delete/<memo_id>", methods=["POST"])
def delete_memo(memo_id):
    memos = load_memos()
    original_len = len(memos)
    memos = [m for m in memos if str(m.get("id", "")) != str(memo_id)]
    if len(memos) < original_len:
        save_memos(memos)
        flash("备忘录删除成功！")
    else:
        flash("备忘录不存在！")
    return redirect(url_for("index"))


@app.route("/pin/<memo_id>", methods=["POST"])
def pin_memo(memo_id):
    memos = load_memos()
    memo = next((m for m in memos if m["id"] == memo_id), None)
    if memo:
        memo["pinned"] = not memo.get("pinned", False)
        save_memos(memos)
    return redirect(url_for("index"))


@app.route("/health")
def health_check():
    data_ok = os.path.exists(DATA_FILE)
    backup_ok = os.path.exists(BACKUP_FILE)
    status = "healthy" if data_ok else "degraded"
    return jsonify(
        {"status": status, "data_file": data_ok, "backup_file": backup_ok}
    ), 200 if data_ok else 503


if __name__ == "__main__":
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"启动应用: debug={debug}, port={port}")

    if os.environ.get("AUTO_OPEN", "false").lower() == "true":
        import webbrowser

        url = f"http://127.0.0.1:{port}"
        webbrowser.open(url)
        logger.info(f"已自动打开浏览器: {url}")

    app.run(host="127.0.0.1", port=port, debug=debug)
