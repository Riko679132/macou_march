#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
app.py —— 澳门绿色通行证 (Macau Green Travel Pass) · Streamlit 演示页

运行：
    d:\\数据库\\.venv\\Scripts\\streamlit.exe run app.py
    或（本仓库 .venv 内） streamlit run app.py

设计：
    输入携带物品 + 通关方向 → 一键生成红/黄/绿通行证（法务判定）
    + 双碳报告（碳足迹 + 澳门本土平替建议）。
    极简单列布局，中英双语可切换；判定逻辑 100% 复用 agent_brain。
"""
from __future__ import annotations

import hashlib
import io
import json
import sqlite3

import streamlit as st
import streamlit.components.v1 as components

import agent_brain as ab

# ---------------------------------------------------------------------------
# 页面配置
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="澳门绿色通行证 · Macau Green Travel Pass",
    page_icon="🟢",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# ---------------------------------------------------------------------------
# 文案（中 / 英）
# ---------------------------------------------------------------------------
STR = {
    "zh": {
        "title": "澳门绿色通行证",
        "subtitle": "安全、绿色出行",
        "switch": "语言",
        "direction": "通关方向",
        "dirs": ["入境澳门", "返回内地"],
        "input": "您携带的物品",
        "placeholder": "示例：我带了2公斤正规超市包装的日本和牛，带齐了检疫证书，要带回内地。",
        "photo_tools": "拍照 / 扫码录入",
        "upload_tab": "上传照片",
        "scan_tab": "实时扫码",
        "upload_label": "选择物品或配料表照片",
        "scan_label": "用摄像头扫描物品或配料表",
        "camera_toggle": "启用摄像头",
        "camera_off_hint": "摄像头已关闭；需要扫码时再打开。",
        "ocr_ok": "已识别文字并填入下方输入框。",
        "ocr_none": "未识别到文字，请调整角度或改用文本输入。",
        "ocr_noengine": "当前环境未启用 OCR，请改用文本输入。",
        "ocr_err": "OCR 识别出错：",
        "generate": "生成通行证",
        "empty": "请先描述您携带的物品。",
        "spinner": "正在判定，请稍候…",
        "error": "生成失败，请稍后重试。",
        "input_error_title": "数量单位需要修正",
        "web_used": "已联网检索参考（以官方数据库为准）",
        "crowd_title": "查不到？帮我们补充",
        "crowd_name": "物品名称",
        "crowd_desc": "简要描述",
        "crowd_desc_ph": "如：一包奥利奥 / 2公斤苹果",
        "crowd_status": "您认为的合规状态",
        "crowd_opts": ["🟢 放行", "🟡 申报", "🔴 违禁"],
        "crowd_submit": "提交审核",
        "crowd_thanks": "感谢您的贡献！您的提交已进入审核队列，预计 24 小时内生效，这期间请以海关现场判定为准。",
        "crowd_error": "提交失败，请稍后重试。",
        "status": {
            "green": ("绿码", "畅通无阻"),
            "yellow": ("黄码", "需人工复核"),
            "red": ("红码", "禁止携带"),
        },
        "reason": "判定说明",
        "suggestion": "行动建议",
        "carbon": "碳足迹估算",
        "carbon_none": "本次未匹配到碳排因子，暂不提供碳足迹估算。",
        "alt": "减碳建议 · 澳门本土平替",
        "direction_meta": "通关方向",
        "colon": "：",
        "footer": "本结果仅供参考，最终以海关现场判定为准，不构成法律意见。",
    },
    "en": {
        "title": "Macau Green Travel Pass",
        "subtitle": "Safe, Green Travel",
        "switch": "Language",
        "direction": "Travel direction",
        "dirs": ["Entering Macau", "Returning to Mainland"],
        "input": "What are you bringing?",
        "placeholder": "Example: I'm bringing 2 kg of packaged Wagyu beef with a valid quarantine certificate back to the mainland.",
        "photo_tools": "Photo / Scan input",
        "upload_tab": "Upload photo",
        "scan_tab": "Live scan",
        "upload_label": "Choose a photo of the item or ingredient label",
        "scan_label": "Scan the item or ingredient label with camera",
        "camera_toggle": "Enable camera",
        "camera_off_hint": "Camera is off. Turn it on to scan.",
        "ocr_ok": "Recognized the text and filled it into the box below.",
        "ocr_none": "No text recognized. Try again or type it manually.",
        "ocr_noengine": "OCR is not available here; please type instead.",
        "ocr_err": "OCR error: ",
        "generate": "Generate Pass",
        "empty": "Please describe the items you are bringing.",
        "spinner": "Checking your items, please wait…",
        "error": "Something went wrong. Please try again.",
        "input_error_title": "Please fix the quantity unit",
        "web_used": "Web references (official database takes precedence)",
        "crowd_title": "Can't find it? Help us add it",
        "crowd_name": "Item name",
        "crowd_desc": "Brief description",
        "crowd_desc_ph": "e.g., a pack of Oreos / 2 kg of apples",
        "crowd_status": "Your compliance view",
        "crowd_opts": ["🟢 Allowed", "🟡 Declare", "🔴 Prohibited"],
        "crowd_submit": "Submit for review",
        "crowd_thanks": "Thank you! Your submission is queued for review and will take effect within about 24 hours. Until then, follow the on-site customs decision.",
        "crowd_error": "Submission failed. Please try again.",
        "status": {
            "green": ("Green Pass", "Good to go"),
            "yellow": ("Yellow Pass", "Manual review needed"),
            "red": ("Red Pass", "Prohibited"),
        },
        "reason": "Reason",
        "suggestion": "What to do",
        "carbon": "Estimated carbon footprint",
        "carbon_none": "No emission factor matched, so no carbon estimate is provided.",
        "alt": "Lower-carbon alternative · Local Macau picks",
        "direction_meta": "Direction",
        "colon": ": ",
        "footer": "For reference only. The on-site customs decision is final. This is not legal advice.",
    },
}

DIRECTION_CN = ["内地入境澳门", "澳门入境内地"]

STATUS_CLASS = {"green": "pass-green", "yellow": "pass-yellow", "red": "pass-red"}

CSS = """
.block-container {max-width: 760px; padding-top: 2.2rem; padding-bottom: 3rem;}
[data-testid="stToolbar"], [data-testid="stAppDeployButton"],
#MainMenu, [data-testid="stMainMenu"], [data-testid="stDecoration"] {
  display: none !important; visibility: hidden !important;}
header[data-testid="stHeader"] {background: transparent;}
html, body, [class*="css"] {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
               "Microsoft YaHei", "Noto Sans SC", sans-serif;
}
.app-title {text-align:center; font-size:34px; font-weight:800; color:#1E3A8A;
            letter-spacing:1px; line-height:1.2;}
.app-sub {text-align:center; font-size:16px; color:#475569; margin:6px 0 18px;}
.mode-label {text-align:center; font-size:13px; font-weight:700; color:#475569;
             letter-spacing:.5px; margin:2px 0 4px;}
.section-label {font-size:13px; font-weight:700; color:#1E3A8A; letter-spacing:.4px;
                margin:14px 0 6px;}
[data-testid="stSegmentedControl"] button {font-size:15px; font-weight:600; padding:8px 16px;}
[data-testid="stSegmentedControl"] {justify-content:center;}
.pass {border-radius:18px; padding:26px 24px; margin:6px 0 18px;
       text-align:center; border:2px solid; transition:all .2s ease;}
.pass-green  {background:#F0FDF4; border-color:#16A34A;}
.pass-yellow {background:#FEFCE8; border-color:#CA8A04;}
.pass-red    {background:#FEF2F2; border-color:#DC2626;}
.pass-title {font-size:38px; font-weight:800; letter-spacing:2px; line-height:1.15;}
.pass-green  .pass-title {color:#166534;}
.pass-yellow .pass-title {color:#854D0E;}
.pass-red    .pass-title {color:#991B1B;}
.pass-sub {font-size:18px; font-weight:600; margin-top:4px; color:#374151;}
.pass-reason {font-size:15px; line-height:1.7; text-align:left; color:#1F2937;
              background:rgba(255,255,255,.72); border-radius:10px;
              padding:14px 16px; margin-top:16px;}
.pass-meta {font-size:13px; color:#4B5563; margin-top:12px;}
.carbon {border-radius:14px; padding:18px 20px; margin:12px 0;
         background:#EFF6FF; border:1px solid #BFDBFE;}
.carbon-label {font-size:14px; color:#1D4ED8; font-weight:600;}
.carbon-value {font-size:32px; font-weight:800; color:#1E3A8A; line-height:1.2;}
.alt {border-radius:14px; padding:16px 18px; margin:8px 0 16px;
      background:#F0FDF4; border:1px solid #BBF7D0; color:#14532D;
      font-size:15px; line-height:1.7;}
.input-error {border-radius:16px; padding:22px; margin:8px 0 14px;
              background:#FFF7ED; border:2px solid #EA580C;}
.ie-title {font-size:22px; font-weight:800; color:#9A3412; margin-bottom:8px;}
.ie-body {font-size:15px; line-height:1.75; color:#7C2D12;}
.stButton>button, .stFormSubmitButton>button {
                  background:#EA580C; color:#fff; border:none; border-radius:10px;
                  padding:12px 18px; font-weight:600; min-height:44px; width:100%;}
.stButton>button:hover, .stFormSubmitButton>button:hover {background:#C2410C; color:#fff;}
.stButton>button:focus-visible, .stFormSubmitButton>button:focus-visible {
                  outline:3px solid #1E3A8A; outline-offset:2px;}
"""
st.markdown(f"<style>{CSS}</style>", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def esc(text) -> str:
    return (str(text) if text is not None else "").replace("&", "&amp;") \
        .replace("<", "&lt;").replace(">", "&gt;")


@st.cache_data(show_spinner=False, ttl=86400)
def translate_batch(texts: tuple) -> tuple:
    """把中文结果批量翻成英文（一次调用）；无 Key/失败时原样返回。"""
    clean = [t for t in texts if t]
    if not clean:
        return texts
    cfg = ab.load_config()
    key, _ = ab.resolve_api_key(cfg)
    if not key:
        return texts
    try:
        from openai import OpenAI
        client = OpenAI(api_key=key, base_url=cfg["deepseek"]["base_url"], timeout=30)
        resp = client.chat.completions.create(
            model=cfg["deepseek"]["model"],
            messages=[
                {"role": "system", "content": (
                    "You are a precise translator. Translate each Chinese string in the "
                    "given JSON array into concise, natural English for a general audience. "
                    "Keep the meaning and all numbers exactly. Return ONLY JSON of the form "
                    '{"t": ["translation 1", ...]} with the same order and count.')},
                {"role": "user", "content": json.dumps(clean, ensure_ascii=False)},
            ],
            temperature=0,
            max_tokens=800,
            response_format={"type": "json_object"},
        )
        out = json.loads(resp.choices[0].message.content or "{}").get("t") or []
        if len(out) != len(clean):
            return texts
        it = iter(out)
        return tuple(next(it) if t else t for t in texts)
    except Exception:
        return texts


@st.cache_resource(show_spinner=False)
def ocr_engine():
    try:
        from rapidocr_onnxruntime import RapidOCR
        return RapidOCR()
    except Exception:
        return None


def ocr_text(img_bytes: bytes) -> str:
    engine = ocr_engine()
    if engine is None:
        return "__NOENGINE__"
    try:
        import numpy as np
        from PIL import Image
        img = np.array(Image.open(io.BytesIO(img_bytes)).convert("RGB"))
        result, _ = engine(img)
        if not result:
            return ""
        return " ".join(line[1] for line in result).strip()
    except Exception as exc:
        return "__ERR__" + type(exc).__name__


def ingest_image(img_bytes: bytes, t: dict) -> None:
    """OCR 识别图片文字并填入输入框（同一张图去重，避免重复追加）。"""
    h = hashlib.md5(img_bytes).hexdigest()
    if st.session_state.get("_ocr_hash") == h:
        return
    st.session_state["_ocr_hash"] = h
    text = ocr_text(img_bytes)
    if text == "__NOENGINE__":
        st.warning(t["ocr_noengine"])
        return
    if text.startswith("__ERR__"):
        st.error(t["ocr_err"] + text[len("__ERR__"):])
        return
    if not text:
        st.warning(t["ocr_none"])
        return
    base = (st.session_state.get("desc_box") or "").strip()
    st.session_state["desc_box"] = (base + "\n" + text).strip() if base else text
    st.success(t["ocr_ok"])


def localize_native(lang: str) -> None:
    """汉化 Streamlit 内置控件文案（摄像头授权、上传按钮等无法直接配置的英文）。"""
    mapping = {} if lang == "en" else {
        "This app would like to use your camera.": "此应用需要使用摄像头。",
        "Learn how to allow access.": "了解如何授权使用摄像头。",
        "Take Photo": "拍照",
        "Drag and drop file here": "将文件拖到此处",
        "Browse files": "浏览文件",
        "Add files": "添加文件",
        "Upload": "上传",
        "200MB per file": "每个文件最大 200MB",
        "Limit 200MB per file": "每个文件最大 200MB",
        "Press Ctrl+Enter to submit form": "按 Ctrl+Enter 提交",
        "Press Ctrl+Enter to apply": "按 Ctrl+Enter 应用",
        "Running...": "运行中…",
        "Stop": "停止",
        "Skip to main content": "跳到主要内容",
    }
    js = """
    (function () {
      var w = window.parent, doc = w.document;
      var M = __MAP__;
      var P = w.__stI18n || (w.__stI18n = {orig: new WeakMap(), obs: null});
      P.map = M;
      function pass() {
        var walker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT, null), n;
        while ((n = walker.nextNode())) {
          var txt = n.nodeValue && n.nodeValue.trim();
          if (!txt) continue;
          var key = P.orig.has(n) ? P.orig.get(n) : txt;
          var val = P.map[key];
          if (val === undefined && /^Remove /.test(key)) val = key.replace(/^Remove /, '移除 ');
          if (val !== undefined) {
            if (!P.orig.has(n)) P.orig.set(n, key);
            if (txt !== val) n.nodeValue = n.nodeValue.replace(key, val);
          } else if (P.orig.has(n)) {
            var o = P.orig.get(n);
            if (txt !== o) n.nodeValue = n.nodeValue.replace(txt, o);
          }
        }
      }
      if (P.obs) { try { P.obs.disconnect(); } catch (e) {} }
      P.obs = new MutationObserver(pass);
      P.obs.observe(doc.body, {childList: true, subtree: true, characterData: true});
      pass();
    })();
    """.replace("__MAP__", json.dumps(mapping, ensure_ascii=False))
    components.html("<script>%s</script>" % js, height=0)


def render_pass(result: dict, t: dict, direction_label: str) -> None:
    status = str(result.get("status", "yellow")).lower()
    name, sub = t["status"].get(status, (status, ""))
    cls = STATUS_CLASS.get(status, "pass-yellow")
    reason = esc(result.get("reason") or "")
    st.markdown(
        f"""
        <div class="pass {cls}">
          <div class="pass-title">{esc(name)}</div>
          <div class="pass-sub">{esc(sub)}</div>
          <div class="pass-reason"><b>{esc(t['reason'])}</b><br>{reason}</div>
          <div class="pass-meta">{esc(t['direction_meta'])}{esc(t['colon'])}{esc(direction_label)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_carbon(result: dict, t: dict) -> None:
    cf = result.get("carbon_footprint")
    if cf is None:
        st.caption(t["carbon_none"])
        return
    unit = result.get("carbon_unit", "kg CO2e")
    st.markdown(
        f"""
        <div class="carbon">
          <div class="carbon-label">{esc(t['carbon'])}</div>
          <div class="carbon-value">{esc(cf)} {esc(unit)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    alt = result.get("alternative_suggestion")
    if alt:
        st.markdown(
            f'<div class="alt"><b>{esc(t["alt"])}</b><br>{esc(alt)}</div>',
            unsafe_allow_html=True,
        )


def render_error(result: dict, t: dict) -> None:
    st.markdown(
        f'<div class="input-error"><div class="ie-title">{esc(t["input_error_title"])}</div>'
        f'<div class="ie-body">{esc(result.get("reason"))}</div></div>',
        unsafe_allow_html=True,
    )
    if result.get("suggestion"):
        st.info(result["suggestion"])


INFO_GAP_KEYWORDS = ("信息不足", "未知", "查不到", "不明确", "存疑", "无法确定",
                     "未找到", "没有找到", "缺乏", "不确定")


def is_info_gap(result: dict) -> bool:
    return (str(result.get("status", "")).lower() == "yellow"
            and any(k in (result.get("reason") or "") for k in INFO_GAP_KEYWORDS))


def insert_pending(name: str, desc: str, user_status: str) -> None:
    db_path = ab.load_config()["database"]["path"]
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO pending_products (product_name, description, user_status, "
            "submit_time, review_status) VALUES (?,?,?,datetime('now','localtime'),'pending')",
            (name, desc, user_status))
        conn.commit()
    finally:
        conn.close()


def render_crowd_form(result: dict, t: dict) -> None:
    if st.session_state.get("pending_done"):
        st.success(t["crowd_thanks"])
        return
    with st.form("crowd_form", border=True):
        st.markdown(f"**{t['crowd_title']}**")
        name = st.text_input(t["crowd_name"],
                             value=(st.session_state.get("last_input") or "")[:60])
        desc = st.text_input(t["crowd_desc"], placeholder=t["crowd_desc_ph"])
        opt = st.radio(t["crowd_status"], t["crowd_opts"], horizontal=True)
        ok = st.form_submit_button(t["crowd_submit"])
    if ok:
        if not (name or "").strip():
            st.warning(t["crowd_name"])
            return
        status = ["green", "yellow", "red"][t["crowd_opts"].index(opt)]
        try:
            insert_pending(name.strip(), desc.strip(), status)
            st.session_state["pending_done"] = True
            st.success(t["crowd_thanks"])
        except Exception:
            st.error(t["crowd_error"])


# ---------------------------------------------------------------------------
# 顶部：语言切换 + 标题
# ---------------------------------------------------------------------------
head_l, head_r = st.columns([3, 2], vertical_alignment="center")
with head_r:
    lang_choice = st.segmented_control(
        STR["zh"]["switch"], ["中文", "English"], default="中文",
        key="lang", label_visibility="collapsed",
    )
lang = "en" if lang_choice == "English" else "zh"
t = STR[lang]

st.markdown(
    f'<div class="app-title">{esc(t["title"])}</div>'
    f'<div class="app-sub">{esc(t["subtitle"])}</div>',
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# 通关方向：标题下方，作为最高级模式切换（值用 0/1，切语言不变）
# ---------------------------------------------------------------------------
st.markdown(f'<div class="mode-label">{esc(t["direction"])}</div>',
            unsafe_allow_html=True)
dir_idx = st.segmented_control(
    t["direction"], options=[0, 1], format_func=lambda i: t["dirs"][i],
    default=0, key="dir_mode", label_visibility="collapsed",
)
if dir_idx is None:
    dir_idx = 0

# ---------------------------------------------------------------------------
# 拍照 / 扫码录入（直接展示，不折叠）
# ---------------------------------------------------------------------------
st.markdown(f'<div class="section-label">{esc(t["photo_tools"])}</div>',
            unsafe_allow_html=True)
col_up, col_scan = st.columns(2)
with col_up:
    st.caption(t["upload_tab"])
    uploaded = st.file_uploader(
        t["upload_label"], type=["jpg", "jpeg", "png"],
        label_visibility="collapsed",
    )
    if uploaded is not None:
        ingest_image(uploaded.getvalue(), t)
with col_scan:
    st.caption(t["scan_tab"])
    cam_on = st.toggle(t["camera_toggle"], value=False, key="camera_on")
    if cam_on:
        shot = st.camera_input(t["scan_label"], label_visibility="collapsed")
        if shot is not None:
            ingest_image(shot.getvalue(), t)
    else:
        st.caption(t["camera_off_hint"])

localize_native(lang)

# ---------------------------------------------------------------------------
# 输入区
# ---------------------------------------------------------------------------
with st.form("pass_form", clear_on_submit=False):
    st.text_area(
        t["input"], height=110, placeholder=t["placeholder"], key="desc_box",
    )
    submitted = st.form_submit_button(
        t["generate"], type="primary", use_container_width=True,
    )

if submitted:
    text = (st.session_state.get("desc_box") or "").strip()
    if not text:
        st.warning(t["empty"])
    else:
        with st.spinner(t["spinner"]):
            try:
                result = ab.analyze_item(text, DIRECTION_CN[dir_idx])
            except Exception:
                result = None
        if result is None:
            st.error(t["error"])
        else:
            st.session_state["last_result"] = result
            st.session_state["last_dir_idx"] = dir_idx
            st.session_state["last_input"] = text
            st.session_state["pending_done"] = False

# ---------------------------------------------------------------------------
# 结果区（语言切换后按新语言重渲染）
# ---------------------------------------------------------------------------
result = st.session_state.get("last_result")
if result:
    result_dir_idx = st.session_state.get("last_dir_idx", dir_idx)
    result_dir_label = t["dirs"][result_dir_idx]
    show_crowd = is_info_gap(result)   # 用翻译前的原文判断

    reason = result.get("reason") or ""
    suggestion = result.get("suggestion") or ""
    alt = result.get("alternative_suggestion") or ""
    if lang == "en":
        reason, suggestion, alt = translate_batch((reason, suggestion, alt))
        result = dict(result, reason=reason, suggestion=suggestion,
                      alternative_suggestion=alt)

    if str(result.get("status")).lower() == "error" or result.get("input_error"):
        render_error(result, t)
    else:
        render_pass(result, t, result_dir_label)
        if suggestion:
            st.info(f"{t['suggestion']}：{suggestion}" if lang == "zh"
                    else f"{t['suggestion']}: {suggestion}")
        render_carbon(result, t)
        if show_crowd:
            render_crowd_form(result, t)
    sources = result.get("web_sources") or []
    if sources:
        links = " · ".join(
            "[%s](%s)" % (s.get("domain") or "link", s.get("url")) for s in sources)
        st.caption((f"{t['web_used']}：" if lang == "zh" else f"{t['web_used']}: ") + links)
    st.caption(t["footer"])
else:
    st.caption(t["footer"])
