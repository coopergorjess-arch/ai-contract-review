import os
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional, List, Tuple

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware

import httpx

from docx import Document  # python-docx


# -----------------------------
# App
# -----------------------------
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------
# DeepSeek HTTP client
# -----------------------------
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")
if not DEEPSEEK_API_KEY:
    raise RuntimeError("DEEPSEEK_API_KEY not found in environment/.env")

DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")


def _call_deepseek_chat(messages: List[Dict[str, str]], temperature: float) -> str:
    url = DEEPSEEK_BASE_URL.rstrip("/") + "/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": messages,
        "temperature": temperature,
    }
    try:
        resp = httpx.post(url, headers=headers, json=payload, timeout=60)
        resp.raise_for_status()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LLM_CALL_FAILED: {e}")
    try:
        data = resp.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LLM_OUTPUT_NOT_JSON: {e}")
    choices = data.get("choices") or []
    if not choices:
        raise HTTPException(status_code=500, detail="LLM_EMPTY_CHOICES")
    content = choices[0].get("message", {}).get("content", "")
    return (content or "").strip()


# -----------------------------
# File read helpers
# -----------------------------
def _read_txt_like(upload: UploadFile) -> str:
    raw = upload.file.read()
    text = raw.decode("utf-8", errors="ignore").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Empty file content.")
    return text


# -----------------------------
# 条款编号抽取器
# -----------------------------
class ClauseNumberExtractor:
    """
    条款编号抽取器：识别并规范化各种编号格式
    维护编号上下文栈，处理嵌套编号
    """
    
    def __init__(self):
        self.clause_stack: List[str] = []  # 编号上下文栈
        self.clause_counter: Dict[int, int] = {}  # 各级别计数器 {level: count}
        
    def extract_clause_number(self, line: str) -> Optional[Dict[str, Any]]:
        """
        从行中提取条款编号
        返回: {"level": 层级, "number": 编号字符串, "normalized": "第X条"格式}
        """
        line = line.strip()
        if not line:
            return None
        
        # 模式1: 第X条、第X.Y条、第X.Y.Z条
        m = re.match(r'^第\s*(\d+(?:\.\d+)*)\s*条', line)
        if m:
            num_str = m.group(1)
            parts = num_str.split('.')
            level = len(parts)
            normalized = f"第{num_str}条"
            return {"level": level, "number": num_str, "normalized": normalized, "raw": m.group(0), "type": "digit_article"}
        
        # 模式2: 第X条（中文数字）
        m = re.match(r'^第\s*([一二三四五六七八九十百千]+)\s*条', line)
        if m:
            cn_num = m.group(1)
            num = _cn_to_int(cn_num)
            if num is not None:
                level = 1
                normalized = f"第{num}条"
                return {"level": level, "number": str(num), "normalized": normalized, "raw": m.group(0), "type": "cn_article"}
        
        # 模式2.1: 中文章节标题，如「三、合同金额及详细配置」「四、付款及结算方式」
        # 这些在很多项目合同里其实就是顶层条款，这里也统一规范成「第X条」
        m = re.match(r'^\s*([一二三四五六七八九十]+)[、\.]\s*', line)
        if m:
            cn_num = m.group(1)
            num = _cn_to_int(cn_num)
            if num is not None:
                level = 1
                normalized = f"第{num}条"
                return {"level": level, "number": str(num), "normalized": normalized, "raw": m.group(0), "type": "cn_header"}
        
        # 模式3: （一）、（二）等（一级编号）
        m = re.match(r'^[（(]\s*([一二三四五六七八九十]+)\s*[）)]', line)
        if m:
            cn_num = m.group(1)
            num = _cn_to_int(cn_num)
            if num is not None:
                level = 1
                normalized = f"第{num}条"
                return {"level": level, "number": str(num), "normalized": normalized, "raw": m.group(0), "type": "cn_paren"}
        
        # 模式4: 1.、2.、3.1. 或 2.1  等（可能是条款编号）
        # 注意：这个模式比较宽泛，需要结合上下文判断
        m = re.match(r'^(\d+(?:\.\d+)*)[\.、）)]\s+', line)
        if not m:
            m = re.match(r'^(\d+(?:\.\d+)*)\s+', line)
        if m:
            num_str = m.group(1)
            parts = num_str.split('.')
            # 如果编号较小（<=20），可能是条款编号
            if len(parts) == 1 and int(parts[0]) <= 20:
                # 检查上下文：如果当前上下文是“强标题”（如中文第X条、X、），则这可能是子条款
                if self.clause_stack and self.clause_stack[-1]["level"] == 1:
                    parent_type = self.clause_stack[-1].get("type", "")
                    # 如果父级是中文数字标题，那么 "1." 应该是子条款（Level 2）
                    if parent_type in ["cn_article", "cn_header"]:
                        level = 2
                        parent_num = self.clause_stack[-1]["number"]
                        normalized = f"第{parent_num}.{num_str}条"
                        return {"level": level, "number": num_str, "normalized": normalized, "raw": m.group(0), "type": "digit_dot_sub"}

                level = 1
                normalized = f"第{num_str}条"
                return {"level": level, "number": num_str, "normalized": normalized, "raw": m.group(0), "type": "digit_dot"}
            elif len(parts) == 2:
                # 可能是子条款，如 3.1
                level = 2
                normalized = f"第{num_str}条"
                return {"level": level, "number": num_str, "normalized": normalized, "raw": m.group(0), "type": "digit_dot_multi"}
        
        return None
    
    def update_stack(self, clause_info: Dict[str, Any]) -> str:
        """
        更新编号上下文栈，返回规范化编号字符串
        """
        level = clause_info["level"]
        normalized = clause_info["normalized"]
        
        # 维护栈：只保留当前层级及以上的编号
        self.clause_stack = [c for c in self.clause_stack if c["level"] < level]
        self.clause_stack.append(clause_info)
        
        # 构建完整编号路径（如：第3条 > 第3.1条）
        if len(self.clause_stack) > 1:
            # 有父级编号，构建完整路径
            parent = self.clause_stack[-2]
            # 简化：只使用当前编号
            return normalized
        else:
            return normalized
    
    def get_current_clause_prefix(self) -> Optional[str]:
        """
        获取当前条款编号前缀（用于非条款行）
        """
        if self.clause_stack:
            return self.clause_stack[-1]["normalized"]
        return None

    def get_current_main_article_number(self) -> str:
        """
        获取当前主条款编号（第X条中的X）
        """
        for c in reversed(self.clause_stack):
            if c["level"] == 1:
                return c["number"]
        return "1"  # Default

    def reset(self):
        """重置状态"""
        self.clause_stack = []
        self.clause_counter = {}


def _has_bullet_prefix_for_line(line: str) -> bool:
    patterns = [
        r"^[\s\u2000-\u200B\u2028-\u2029]*[•▪▫○●◆◇■□▲△▼▽\-*→▶▷◀◁◦‣⁃]\s*",
        r"^[\s]*\d+[\.、）)]\s+",
        r"^[\s]*[（(]\d+[）)]\s+",
        r"^[\s]*[a-zA-Z][\.、）)]\s+",
        r"^[\s]*[（(][a-zA-Z][）)]\s+",
        r"^[\s]*[一二三四五六七八九十]+[\.、）)]\s+",
        r"^[\s]*[（(][一二三四五六七八九十]+[）)]\s+",
        r"^[\s]*[iIvVxXlLcCdDmM]+[\.、）)]\s+",
        r"^[\s\t]*\d+[\.、）)]\s+",
        r"^[\s\t]*[（(]\d+[）)]\s+",
        r"^[\u00A0\u2000-\u200B\u202F\u3000]+\d+[\.、）)]\s+",
    ]
    for p in patterns:
        if re.match(p, line or ""):
            return True
    return False


def _docx_list_level(paragraph) -> Tuple[bool, int]:
    try:
        pPr = getattr(paragraph._p, "pPr", None)
        if pPr is None:
            return False, -1
        numPr = getattr(pPr, "numPr", None)
        if numPr is None:
            return False, -1
        ilvl = getattr(numPr, "ilvl", None)
        lvl = int(getattr(ilvl, "val", 0)) if ilvl is not None else 0
        return True, lvl
    except Exception:
        return False, -1


def _docx_indent_level(paragraph) -> int:
    try:
        fmt = getattr(paragraph, "paragraph_format", None)
        if fmt is None:
            return 0
        left = getattr(fmt, "left_indent", None)
        if left is None:
            return 0
        pt = getattr(left, "pt", None)
        if pt is None:
            return 0
        try:
            v = float(pt)
        except Exception:
            return 0
        if v <= 0:
            return 0
        return max(0, int(round(v / 18.0)))
    except Exception:
        return 0


def _docx_is_bold_paragraph(paragraph) -> bool:
    try:
        runs = getattr(paragraph, "runs", None) or []
        for r in runs:
            if not (getattr(r, "text", "") or "").strip():
                continue
            if getattr(r, "bold", False):
                return True
        return False
    except Exception:
        return False


_SIG_STRONG_RE = re.compile(r"(签署|签字|盖章|法定代表人|授权代表|委托代理人|开户行|账号|帐号|税号|统一社会信用代码)", re.IGNORECASE)
_SIG_PARTY_LINE_RE = re.compile(r"^\s*(甲方|乙方|丙方|丁方|采购方|供方|承包方|发包方|出租方|承租方|服务方|委托方|受托方)\s*[:：]", re.IGNORECASE)
_SIG_CONTACT_LINE_RE = re.compile(r"^\s*(单位名称|地址|电话|传真)\s*[:：]", re.IGNORECASE)
_REMARK_RE = re.compile(r"^(备注|说明|注[:：]|特别说明)[:：]?", re.IGNORECASE)


def _looks_like_signature(text: str) -> bool:
    s = (text or "").strip()
    if not s:
        return False
    if _SIG_STRONG_RE.search(s):
        return True
    if _SIG_PARTY_LINE_RE.search(s):
        return True
    if _SIG_CONTACT_LINE_RE.search(s):
        return True
    if re.search(r"^\s*(签署页|签字页)\s*$", s):
        return True
    return False


def _looks_like_remark(text: str) -> bool:
    s = (text or "").strip()
    if not s:
        return False
    if _REMARK_RE.search(s):
        return True
    return False


def _looks_like_title(text: str, is_bold: bool) -> bool:
    s = (text or "").strip()
    if not s:
        return False
    if re.match(r"^第\s*([0-9]+(?:\.[0-9]+)*)\s*条", s):
        return True
    if re.match(r"^第\s*([一二三四五六七八九十百千]+)\s*条", s):
        return True
    if re.match(r"^\s*[一二三四五六七八九十]+\s*[、\.]\s*", s):
        return True
    if re.match(r"^\s*\d+\s*[、\.]\s*", s) and len(s) <= 40:
        return True
    if is_bold and len(s) <= 60 and not re.search(r"[。；;.!?]", s):
        return True
    if len(s) <= 60 and not re.search(r"[。；;.!?]", s):
        keywords = ["责任", "违约", "付款", "价格", "期限", "保密", "知识产权", "不可抗力", "争议", "解除", "终止"]
        if any(kw in s for kw in keywords):
            return True
    return False


def _extract_explicit_main_number(text: str) -> Optional[int]:
    s = (text or "").strip()
    if not s:
        return None
    m = re.match(r"^第\s*([0-9]+)\s*条", s)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            return None
    m = re.match(r"^第\s*([一二三四五六七八九十百千]+)\s*条", s)
    if m:
        v = _cn_to_int(m.group(1))
        return v
    m = re.match(r"^\s*([一二三四五六七八九十]+)\s*[、\.]\s*", s)
    if m:
        v = _cn_to_int(m.group(1))
        return v
    m = re.match(r"^\s*([0-9]+)\s*[、\.]\s*", s)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            return None
    return None


def _segment_docx_structure(docx_path: Path) -> List[Dict[str, Any]]:
    doc = Document(str(docx_path))
    segments: List[Dict[str, Any]] = []
    idx = 0

    for p in doc.paragraphs:
        raw = (p.text or "")
        raw_stripped = raw.strip()
        if not raw_stripped:
            continue
        idx += 1
        cleaned = _clean_bullet_prefix(raw_stripped)
        is_bullet = _has_bullet_prefix_for_line(raw_stripped)
        is_list, list_level = _docx_list_level(p)
        indent_level = _docx_indent_level(p)
        is_bold = _docx_is_bold_paragraph(p)

        seg_type = "条款正文"
        if _looks_like_signature(cleaned):
            seg_type = "签署信息"
        elif _looks_like_remark(cleaned):
            seg_type = "备注"
        elif _looks_like_title(cleaned, is_bold):
            seg_type = "条款标题"

        segments.append({
            "idx": idx,
            "type": seg_type,
            "text": cleaned or raw_stripped,
            "raw": raw_stripped,
            "is_bullet": bool(is_bullet),
            "is_list": bool(is_list),
            "list_level": int(list_level) if is_list else -1,
            "indent_level": int(indent_level),
            "is_bold": bool(is_bold),
            "source": "paragraph",
        })

    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                ct = (cell.text or "").strip()
                if not ct:
                    continue
                for ln in [x.strip() for x in ct.splitlines() if x.strip()]:
                    idx += 1
                    seg_type = "条款正文"
                    if _looks_like_signature(ln):
                        seg_type = "签署信息"
                    elif _looks_like_remark(ln):
                        seg_type = "备注"
                    elif _looks_like_title(ln, False):
                        seg_type = "条款标题"
                    segments.append({
                        "idx": idx,
                        "type": seg_type,
                        "text": ln,
                        "raw": ln,
                        "is_bullet": bool(_has_bullet_prefix_for_line(ln)),
                        "is_list": False,
                        "list_level": -1,
                        "indent_level": 0,
                        "is_bold": False,
                        "source": "table",
                    })

    return segments


def _build_structure_debug_lines(segments: List[Dict[str, Any]], anomalies: List[str]) -> str:
    lines: List[str] = []
    for seg in segments:
        s = (seg.get("text") or "").strip()
        preview = re.sub(r"\s+", " ", s)[:30]
        bullet = "1" if seg.get("is_bullet") else "0"
        ilvl = seg.get("list_level", -1)
        ilvl_str = str(ilvl) if ilvl is not None and int(ilvl) >= 0 else ""
        indent = str(seg.get("indent_level", 0))
        lines.append(f"{seg.get('idx')}\t{seg.get('type')}\t{preview}\tbullet={bullet}\tilvl={ilvl_str}\tindent={indent}")
    if anomalies:
        lines.append("")
        lines.append("异常清单：")
        lines.extend(anomalies)
    return "\n".join(lines)


def _rebuild_numbered_text_from_segments(segments: List[Dict[str, Any]]) -> Tuple[str, List[str]]:
    out_lines: List[str] = []
    anomalies: List[str] = []

    current_article: Optional[int] = None
    article_auto = 0

    title_depth_stack: List[int] = []
    title_level = 0
    title_sub_counts: List[int] = []

    list_tracker: Dict[int, int] = {}
    current_title_prefix: Optional[str] = None
    has_seen_title = False

    def _set_title_by_level(new_level: int, explicit_article: Optional[int]) -> str:
        nonlocal current_article, article_auto, title_level, title_sub_counts, list_tracker, current_title_prefix
        if new_level <= 1:
            if explicit_article is not None:
                current_article = explicit_article
                if explicit_article > article_auto:
                    article_auto = explicit_article
            else:
                article_auto += 1
                current_article = article_auto
            title_level = 1
            title_sub_counts = []
            list_tracker = {}
            current_title_prefix = f"第{current_article}条"
            return current_title_prefix
        if current_article is None:
            article_auto += 1
            current_article = article_auto
        title_level = new_level
        while len(title_sub_counts) < (new_level - 1):
            title_sub_counts.append(0)
        if len(title_sub_counts) > (new_level - 1):
            title_sub_counts = title_sub_counts[:(new_level - 1)]
        title_sub_counts[new_level - 2] += 1
        for i in range(new_level - 1, len(title_sub_counts)):
            title_sub_counts[i] = 0
        parts = [str(x) for x in title_sub_counts[:(new_level - 1)] if x > 0]
        if parts:
            current_title_prefix = f"第{current_article}." + ".".join(parts) + "条"
        else:
            current_title_prefix = f"第{current_article}条"
        list_tracker = {}
        return current_title_prefix

    def _current_title_subpath_parts() -> List[int]:
        if title_level <= 1:
            return []
        return [x for x in title_sub_counts[:(title_level - 1)] if x > 0]

    for seg in segments:
        seg_type = seg.get("type")
        raw = (seg.get("raw") or "").strip()
        text = (seg.get("text") or "").strip()
        if not text:
            continue

        if seg_type == "条款标题":
            has_seen_title = True
            indent_level = int(seg.get("indent_level", 0) or 0)
            list_level_val = seg.get("list_level", -1)
            try:
                list_level_int = int(list_level_val) if list_level_val is not None else -1
            except Exception:
                list_level_int = -1
            depth_hint = max(indent_level, list_level_int + 1 if list_level_int >= 0 else 0)
            ambiguous = (depth_hint == 0) and (list_level_int == -1) and (indent_level == 0) and (not seg.get("is_bold"))
            if not title_depth_stack:
                title_depth_stack = [depth_hint]
                new_level = 1
            else:
                last_depth = title_depth_stack[-1]
                if depth_hint > last_depth:
                    title_depth_stack.append(depth_hint)
                    new_level = len(title_depth_stack)
                elif depth_hint == last_depth:
                    new_level = len(title_depth_stack)
                else:
                    while title_depth_stack and depth_hint < title_depth_stack[-1]:
                        title_depth_stack.pop()
                    if not title_depth_stack:
                        title_depth_stack = [depth_hint]
                        new_level = 1
                    elif depth_hint == title_depth_stack[-1]:
                        new_level = len(title_depth_stack)
                    else:
                        title_depth_stack.append(depth_hint)
                        new_level = len(title_depth_stack)

            if ambiguous and title_depth_stack and len(title_depth_stack) >= 1:
                preview = re.sub(r"\s+", " ", text)[:30]
                anomalies.append(f"- 段落{seg.get('idx')}: 标题层级无法判断，默认同级：{preview}")

            explicit_article = _extract_explicit_main_number(raw)
            prefix = _set_title_by_level(new_level, explicit_article if new_level == 1 else None)
            title_text = re.sub(r"^(第\s*([0-9]+(?:\.[0-9]+)*)\s*条|第\s*[一二三四五六七八九十百千]+\s*条|[一二三四五六七八九十]+\s*[、\.]|[0-9]+\s*[、\.])\s*", "", text).strip()
            out_lines.append(f"{prefix} {title_text or text}")
            continue

        if seg_type in ("签署信息", "备注"):
            out_lines.append(text)
            continue

        if not has_seen_title:
            out_lines.append(text)
            continue

        if current_article is None:
            article_auto += 1
            current_article = article_auto
            current_title_prefix = f"第{current_article}条"
            title_level = 1
            title_depth_stack = [0]
            title_sub_counts = []
            list_tracker = {}

        is_bullet = bool(seg.get("is_bullet"))
        is_list = bool(seg.get("is_list"))
        list_level_val2 = seg.get("list_level", -1)
        try:
            list_level_raw = int(list_level_val2) if list_level_val2 is not None else -1
        except Exception:
            list_level_raw = -1
        effective_list_level = None
        if is_list and list_level_raw >= 0:
            effective_list_level = list_level_raw + 1
        elif is_bullet:
            effective_list_level = 1

        if effective_list_level is not None:
            keys_to_del = [k for k in list_tracker if k > effective_list_level]
            for k in keys_to_del:
                del list_tracker[k]
            list_tracker[effective_list_level] = list_tracker.get(effective_list_level, 0) + 1
            base_parts = _current_title_subpath_parts()
            parts: List[str] = []
            if base_parts:
                parts.extend([str(x) for x in base_parts])
            for l in range(1, effective_list_level + 1):
                parts.append(str(list_tracker.get(l, 1)))
            prefix = f"第{current_article}." + ".".join(parts) + "条"
            out_lines.append(f"{prefix} {text}")
        else:
            if current_title_prefix:
                if re.match(r"^第\s*[0-9一二三四五六七八九十百千]+", raw):
                    out_lines.append(raw)
                else:
                    out_lines.append(f"{current_title_prefix} {text}")
            else:
                out_lines.append(text)

    final_text = "\n".join(out_lines).strip()
    return final_text, anomalies


def _extract_docx_text_refactored(docx_path: Path, debug: bool = False) -> Tuple[str, Optional[str], str]:
    segments = _segment_docx_structure(docx_path)
    text, anomalies = _rebuild_numbered_text_from_segments(segments)
    debug_text = _build_structure_debug_lines(segments, anomalies) if debug else None
    if not text:
        raise HTTPException(status_code=400, detail="DOCX contains no extractable text (empty paragraphs/tables).")
    raw_lines: List[str] = []
    for seg in segments:
        raw = (seg.get("raw") or "").strip()
        if raw:
            raw_lines.append(raw)
    raw_text = "\n".join(raw_lines).strip()
    return text, debug_text, raw_text


def _extract_docx_text_with_clause_numbers(docx_path: Path) -> str:
    """
    提取 DOCX 文档文本，并将条款编号前缀写入文本
    这是新的实现，将编号直接嵌入文本中
    """
    doc = Document(str(docx_path))
    extractor = ClauseNumberExtractor()
    parts: List[str] = []
    auto_idx = 0
    list_tracker: Dict[int, int] = {}  # 跟踪列表层级计数 {level: count}

    def _is_list_paragraph(paragraph) -> Tuple[bool, int]:
        try:
            pPr = getattr(paragraph._p, "pPr", None)
            if pPr is None:
                return False, -1
            numPr = getattr(pPr, "numPr", None)
            if numPr is None:
                return False, -1
            ilvl = getattr(numPr, "ilvl", None)
            lvl = int(getattr(ilvl, "val", 0)) if ilvl is not None else 0
            return True, lvl
        except Exception:
            return False, -1
    
    def _has_bullet_prefix_for_line(line: str) -> bool:
        patterns = [
            r"^[\s\u2000-\u200B\u2028-\u2029]*[•▪▫○●◆◇■□▲△▼▽\-*→▶▷◀◁◦‣⁃]\s*",
            r"^[\s]*\d+[\.、）)]\s+",
            r"^[\s]*[（(]\d+[）)]\s+",
            r"^[\s]*[a-zA-Z][\.、）)]\s+",
            r"^[\s]*[（(][a-zA-Z][）)]\s+",
            r"^[\s]*[一二三四五六七八九十]+[\.、）)]\s+",
            r"^[\s]*[（(][一二三四五六七八九十]+[）)]\s+",
            r"^[\s]*[iIvVxXlLcCdDmM]+[\.、）)]\s+",
            r"^[\s\t]*\d+[\.、）)]\s+",
            r"^[\s\t]*[（(]\d+[）)]\s+",
            r"^[\u00A0\u2000-\u200B\u202F\u3000]+\d+[\.、）)]\s+",
        ]
        for p in patterns:
            if re.match(p, line):
                return True
        return False
    
    def _is_probable_header(line: str) -> bool:
        s = (line or "").strip()
        if not s:
            return False
        if len(s) < 3 or len(s) > 80:
            return False
        if re.search(r"[。；;.!?]", s) and len(s) > 20:
            return False
        words = re.split(r"[\s、，。；：]+", s)
        if len(words) <= 1:
            return False
        if len(words) > 18:
            return False
        upper_ratio = sum(1 for ch in s if ch.isupper()) / max(1, len(s))
        punct_ratio = sum(1 for ch in s if ch in ",，。.;；：:") / max(1, len(s))
        if upper_ratio > 0.2 and punct_ratio < 0.05:
            return True
        keywords = ["责任", "违约", "付款", "价格", "期限", "保密", "知识产权", "不可抗力",
                    "Liability", "Breach", "Payment", "Price", "Term", "Confidential", "IP", "Force Majeure"]
        if any(kw in s for kw in keywords):
            return True
        return False
    
    for p in doc.paragraphs:
        t = (p.text or "").strip()
        if not t:
            continue
        
        # 尝试提取条款编号
        clause_info = extractor.extract_clause_number(t)
        
        if clause_info:
            # 找到条款编号，更新栈
            clause_prefix = extractor.update_stack(clause_info)
            if clause_info["level"] == 1:
                list_tracker = {}

            # 将编号前缀写入文本
            # 如果文本已经包含编号，保留；否则添加前缀
            if not t.startswith(clause_prefix):
                para_text = f"{clause_prefix} {t}"
            else:
                para_text = t
        else:
            original = t
            cleaned = _clean_bullet_prefix(original)
            is_bullet = _has_bullet_prefix_for_line(original)
            is_header = _is_probable_header(cleaned)
            is_list, list_level = _is_list_paragraph(p)
            if is_bullet or is_header:
                auto_idx += 1
                list_tracker = {}
                auto_info = {"level": 1, "number": str(auto_idx), "normalized": f"第{auto_idx}条", "raw": ""}
                clause_prefix = extractor.update_stack(auto_info)
                if not cleaned.startswith(clause_prefix):
                    para_text = f"{clause_prefix} {cleaned}"
                else:
                    para_text = cleaned
            elif is_list:
                # 检查上下文：如果当前上下文是“强标题”（如中文第X条、X、），则列表项（ilvl=0）可能是子条款
                demote_to_level_2 = False
                if list_level <= 0 and extractor.clause_stack:
                    last = extractor.clause_stack[-1]
                    if last["level"] == 1 and last.get("type") in ["cn_article", "cn_header"]:
                        demote_to_level_2 = True
                
                if list_level <= 0 and not demote_to_level_2:
                    auto_idx += 1
                    list_tracker = {}
                    auto_info = {"level": 1, "number": str(auto_idx), "normalized": f"第{auto_idx}条", "raw": "", "type": "auto_list_l1"}
                    clause_prefix = extractor.update_stack(auto_info)
                    para_text = f"{clause_prefix} {cleaned or original}"
                else:
                    # 子层级生成逻辑
                    # 如果是被降级的 Top-Level List，则视为 Level 2 (list_level = 1)
                    effective_level = list_level if list_level > 0 else 1
                    
                    main_num = extractor.get_current_main_article_number()
                    
                    # 清理更深层级的计数器
                    keys_to_del = [k for k in list_tracker if k > effective_level]
                    for k in keys_to_del: del list_tracker[k]
                    
                    # 递增当前层级
                    list_tracker[effective_level] = list_tracker.get(effective_level, 0) + 1
                    
                    # 生成编号字符串
                    if effective_level == 1:
                        # Level 1 (mapped from ilvl=1): 第X.Y条
                        val = list_tracker[1]
                        full_label = f"第{main_num}.{val}条"
                    elif effective_level == 2:
                        # Level 2 (mapped from ilvl=2): 第X.Y(Z)款
                        val1 = list_tracker.get(1, 1)
                        val2 = list_tracker[2]
                        full_label = f"第{main_num}.{val1}({val2})款"
                    else:
                        # Level > 2: 第X.Y.Z...款
                        parts = []
                        for l in range(1, effective_level + 1):
                            parts.append(str(list_tracker.get(l, 1)))
                        full_label = f"第{main_num}.{'.'.join(parts)}款"
                    
                    stack_level = effective_level + 1
                    auto_info = {
                        "level": stack_level, 
                        "number": full_label,
                        "normalized": full_label, 
                        "raw": "",
                        "type": "auto_list_sub"
                    }
                    clause_prefix = extractor.update_stack(auto_info)
                    para_text = f"{clause_prefix} {cleaned or original}"
            else:
                current_prefix = extractor.get_current_clause_prefix()
                if current_prefix:
                    if not re.match(r'^第\s*\d+', t):
                        para_text = f"{current_prefix} {t}"
                    else:
                        para_text = t
                else:
                    para_text = t
        
        parts.append(para_text)
    
    # 处理表格
    for table in doc.tables:
        for row in table.rows:
            cells = []
            for cell in row.cells:
                ct = (cell.text or "").strip()
                if ct:
                    cells.append(ct)
            if cells:
                parts.append(" | ".join(cells))
    
    text = "\n".join(parts).strip()
    if not text:
        raise HTTPException(status_code=400, detail="DOCX contains no extractable text (empty paragraphs/tables).")
    return text


def _extract_docx_text(docx_path: Path) -> str:
    """
    提取 DOCX 文档文本，使用新的条款编号抽取器
    将条款编号前缀直接写入文本中
    """
    text, _, _ = _extract_docx_text_refactored(docx_path, debug=False)
    return text


def _find_soffice() -> Optional[str]:
    p = shutil.which("soffice")
    if p:
        return p
    candidates = [
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
        "/Applications/LibreOffice.app/Contents/MacOS/soffice.bin",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    return None


def _convert_doc_to_docx(doc_path: Path) -> Path:
    soffice = _find_soffice()
    if not soffice:
        raise HTTPException(
            status_code=400,
            detail="DOC file uploaded but LibreOffice (soffice) not found. Please install LibreOffice to enable .doc conversion.",
        )

    out_dir = doc_path.parent
    cmd = [
        soffice,
        "--headless",
        "--nologo",
        "--nofirststartwizard",
        "--convert-to",
        "docx",
        "--outdir",
        str(out_dir),
        str(doc_path),
    ]

    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except subprocess.CalledProcessError as e:
        err = (e.stderr or e.stdout or "")[:800]
        raise HTTPException(status_code=400, detail=f"DOC conversion failed. LibreOffice error: {err}")

    converted = doc_path.with_suffix(".docx")
    if not converted.exists():
        raise HTTPException(status_code=400, detail="DOC conversion did not produce a .docx output.")
    return converted


def _read_upload_to_text(file: UploadFile, debug_docx: bool = False) -> Tuple[str, Optional[str], str]:
    filename = (file.filename or "").lower()

    if filename.endswith(".txt") or filename.endswith(".md"):
        txt = _read_txt_like(file)
        return txt, None, txt

    if filename.endswith(".docx") or filename.endswith(".doc"):
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            suffix = ".docx" if filename.endswith(".docx") else ".doc"
            in_path = td_path / f"input{suffix}"

            content = file.file.read()
            if not content:
                raise HTTPException(status_code=400, detail="Empty Word file.")
            in_path.write_bytes(content)

            docx_path = in_path
            if suffix == ".doc":
                docx_path = _convert_doc_to_docx(in_path)

            text, debug_text, raw_text = _extract_docx_text_refactored(docx_path, debug=debug_docx)
            return text, debug_text, raw_text

    raise HTTPException(
        status_code=400,
        detail=f"Unsupported file type: {file.filename}. Supported: .txt .md .docx .doc",
    )


# -----------------------------
# JSON parsing (critical)
# -----------------------------
_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}", re.MULTILINE)

def _strip_code_fences(s: str) -> str:
    s = (s or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    return s.strip()

def _extract_json_str(s: str) -> str:
    s = _strip_code_fences(s)
    m = _JSON_BLOCK_RE.search(s)
    if not m:
        return s
    return m.group(0).strip()

def _parse_model_json(raw_text: str) -> Dict[str, Any]:
    candidate = _extract_json_str(raw_text)
    try:
        return json.loads(candidate)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"MODEL_OUTPUT_NOT_JSON: {str(e)} | raw_head={candidate[:200]}")

def _normalize_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    # raw_excerpt: clause -> location
    raw_excerpt = payload.get("raw_excerpt")
    if isinstance(raw_excerpt, list):
        normalized = []
        for it in raw_excerpt:
            if isinstance(it, dict):
                loc = it.get("location") or it.get("clause") or it.get("Clause") or ""
                txt = it.get("text") or ""
                normalized.append({"location": loc, "text": txt})
        payload["raw_excerpt"] = normalized

    # risks: clause -> location，确保 location 字段存在
    risks = payload.get("risks")
    if isinstance(risks, list):
        for r in risks:
            if isinstance(r, dict):
                # 如果 location 不存在，尝试从 clause 字段获取
                if not r.get("location"):
                    if r.get("clause"):
                        r["location"] = r.get("clause")
                    else:
                        r["location"] = ""  # 确保字段存在，后续会补全

    # revisions: 确保 location 字段存在
    revisions = payload.get("revisions")
    if isinstance(revisions, list):
        for rv in revisions:
            if isinstance(rv, dict):
                if "location" not in rv:
                    rv["location"] = ""  # 确保字段存在，后续会补全

    return payload

def _assign_excerpt_ids(payload: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    exs = payload.get("raw_excerpt")
    if not isinstance(exs, list):
        return {}
    id_map: Dict[int, Dict[str, Any]] = {}
    for i, it in enumerate(exs):
        if not isinstance(it, dict):
            continue
        it["id"] = it.get("id") or f"ex_{i+1}"
        id_map[i] = it
    return id_map

def _base_location(s: str) -> str:
    s = (s or "").strip()
    return s.split(" / ", 1)[0].strip()

def _expand_excerpt_context(payload: Dict[str, Any], full_text: str) -> None:
    exs = payload.get("raw_excerpt") or []
    if not isinstance(exs, list):
        return
    txt = (full_text or "").strip()
    if not txt:
        return
    # 简单的句边界：中文句号/问号/叹号/分号/换行
    boundaries = r"[。！？；;]\s*"
    for ex in exs:
        if not isinstance(ex, dict):
            continue
        t = (ex.get("text") or "").strip()
        if not t:
            continue
        if len(t) >= 80 and len(t) <= 240:
            continue
        # 在全文中查找一次精确匹配
        idx = txt.find(t)
        if idx < 0:
            continue
        start = max(0, idx - 200)
        end = min(len(txt), idx + len(t) + 200)
        seg = txt[start:end]
        # 以句边界截断，保证前后各一段上下文
        # 找到片段内第一个句首
        import re
        pre_parts = re.split(boundaries, seg)
        if len(pre_parts) >= 3:
            # 取中间一段，前后各补一段
            new_t = "".join(pre_parts[max(0, len(pre_parts)//2 - 1): min(len(pre_parts), len(pre_parts)//2 + 2)]).strip()
        else:
            new_t = seg.strip()
        if 60 <= len(new_t) <= 300:
            ex["text"] = new_t

def _attach_anchors(payload: Dict[str, Any]) -> None:
    exs = payload.get("raw_excerpt") or []
    ex_by_base: Dict[str, List[Dict[str, Any]]] = {}
    for ex in exs:
        if not isinstance(ex, dict):
            continue
        base = _base_location(ex.get("location", ""))
        ex_by_base.setdefault(base, []).append(ex)
    def _fill_items(items: List[Dict[str, Any]]) -> None:
        for it in items or []:
            if not isinstance(it, dict):
                continue
            base = _base_location(it.get("location", ""))
            cands = ex_by_base.get(base) or []
            if cands:
                # 选择第一条作为绑定
                first = cands[0]
                it["excerpt_ids"] = [first.get("id", "")]
                txt = (first.get("text") or "").strip()
                if txt:
                    it["anchor_text"] = txt[:180]
            else:
                # 没有同 base 的 excerpt，则尝试从自身字段生成 anchor
                anchor_src = (it.get("risk") or it.get("text") or it.get("title") or "")[:200]
                it["anchor_text"] = anchor_src
                it["excerpt_ids"] = it.get("excerpt_ids") or []
    _fill_items(payload.get("risks") or [])
    _fill_items(payload.get("revisions") or [])


# -----------------------------
# Clause indexing & post-fix numbering
# -----------------------------
_CN_NUM = {
    "零":0,"一":1,"二":2,"三":3,"四":4,"五":5,"六":6,"七":7,"八":8,"九":9,
    "十":10,"百":100,"千":1000
}
def _cn_to_int(s: str) -> Optional[int]:
    s = (s or "").strip()
    if not s:
        return None
    # already digits
    if re.fullmatch(r"\d+", s):
        return int(s)

    total = 0
    num = 0
    unit = 1
    has = False
    for ch in reversed(s):
        if ch in ("十","百","千"):
            has = True
            u = _CN_NUM.get(ch, 1)
            unit = u
            if num == 0:
                num = 1
            total += num * unit
            num = 0
            unit = 1
        else:
            v = _CN_NUM.get(ch)
            if v is None:
                return None
            has = True
            num = v
    total += num
    return total if has else None

def _clean_bullet_prefix(line: str) -> str:
    """
    清理行首的项目符号和编号前缀
    例如：
    "• 第一条 合同双方" -> "第一条 合同双方"
    "1. 第一条 合同双方" -> "第一条 合同双方"
    "（1）第一条 合同双方" -> "第一条 合同双方"
    "1) 第一条 合同双方" -> "第一条 合同双方"
    "a. 第一条 合同双方" -> "第一条 合同双方"
    """
    # 匹配常见的项目符号和编号格式
    bullet_patterns = [
        # 项目符号（各种 Unicode 符号，包括制表符等空白字符）
        r"^[\s\u2000-\u200B\u2028-\u2029]*[•▪▫○●◆◇■□▲△▼▽\-*→▶▷◀◁◦‣⁃]\s*",
        # 数字编号：1. 或 1、或 1) 或 1）或 1）或 (1) 或（1）
        r"^[\s]*\d+[\.、）)]\s+",
        r"^[\s]*[（(]\d+[）)]\s+",
        # 字母编号：a. 或 A. 或 a) 或 A)
        r"^[\s]*[a-zA-Z][\.、）)]\s+",
        r"^[\s]*[（(][a-zA-Z][）)]\s+",
        # 中文编号：一、或 一. 或 （一）或 (一)
        r"^[\s]*[一二三四五六七八九十]+[\.、）)]\s+",
        r"^[\s]*[（(][一二三四五六七八九十]+[）)]\s+",
        # 罗马数字编号：i. 或 I. 或 i) 或 I)
        r"^[\s]*[iIvVxXlLcCdDmM]+[\.、）)]\s+",
        # Word 自动编号可能产生的格式：带制表符的编号
        r"^[\s\t]*\d+[\.、）)]\s+",
        r"^[\s\t]*[（(]\d+[）)]\s+",
        # 可能存在的特殊空白字符
        r"^[\u00A0\u2000-\u200B\u202F\u3000]+\d+[\.、）)]\s+",
    ]
    
    cleaned = line
    for pattern in bullet_patterns:
        cleaned = re.sub(pattern, "", cleaned, flags=re.UNICODE)
    
    # 额外处理：如果行首还有制表符或特殊空白，也清理掉
    cleaned = re.sub(r"^[\s\t\u2000-\u200B\u202F\u3000]+", "", cleaned)
    
    return cleaned.strip()


def _extract_clause_candidates(text: str) -> List[Dict[str, str]]:
    """
    返回候选条款列表：
    { "id": "第7条", "title": "付款方式", "line": "第七条 付款方式..." }
    也支持 "第3.1条"、"第3.1(1)款" 这类。
    支持带项目符号的格式，如 "• 第一条" 或 "1. 第一条"
    """
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]  # 先不 strip，保留原始格式
    out: List[Dict[str, str]] = []

    # patterns:
    # 1) 第七条 XXX（支持中文数字）
    p_cn = re.compile(r"第([一二三四五六七八九十百千0-9]+)条\s*(.*)$")
    # 2) 第3.1条 / 第3.1(1)款 / 第3.1.1款（支持数字、点、括号，结尾可是条或款）
    p_num = re.compile(r"第([0-9\.\(\)]+)[条款]\s*(.*)$")
    
    # 3) 仅有中文编号的章节标题，如「七、不可抗力」「三. 付款方式」——视为第N条
    p_cn_header = re.compile(r"^\s*([一二三四五六七八九十百千]+)[、\.]\s*(.*)$")

    # 4) 更宽松的匹配：允许前面有任意字符（项目符号、空格等）
    p_cn_loose = re.compile(r".*?第([一二三四五六七八九十百千0-9]+)条\s*(.*)$")
    p_num_loose = re.compile(r".*?第([0-9\.\(\)]+)[条款]\s*(.*)$")

    for ln in lines:
        original_line = ln
        ln_stripped = ln.strip()
        
        # 先清理项目符号前缀
        cleaned_line = _clean_bullet_prefix(ln_stripped)
        
        # 如果清理后为空，跳过
        if not cleaned_line:
            continue
        
        # 策略1: 在清理后的行中精确匹配（优先）
        m = p_num.search(cleaned_line)
        if m:
            num_part = m.group(1)
            suffix = "款" if "款" in m.group(0) else "条"
            # 简单判断后缀，或者直接用 group(0) 的匹配部分？
            # p_num pattern: 第...[条款]
            # let's reconstruct id carefully
            clause_id = f"第{num_part}{suffix}"
            # actually better to just check if original matched string had 款
            # But regex group 0 includes "第" and "条款"
            # let's capture suffix in regex
            
            title = (m.group(2) or "").strip()
            out.append({"id": clause_id, "title": title, "line": original_line})
            continue

        m = p_cn.search(cleaned_line)
        if m:
            raw_cn = m.group(1)
            n = _cn_to_int(raw_cn)
            if n is not None:
                clause_id = f"第{n}条"
            else:
                clause_id = f"第{raw_cn}条"
            title = (m.group(2) or "").strip()
            out.append({"id": clause_id, "title": title, "line": original_line})
            continue

        # 策略1.1: 仅有中文数字编号的章节标题（例如「七、不可抗力」）
        m = p_cn_header.search(cleaned_line)
        if m:
            raw_cn = m.group(1)
            n = _cn_to_int(raw_cn)
            if n is not None:
                clause_id = f"第{n}条"
            else:
                clause_id = f"第{raw_cn}条"
            title = (m.group(2) or "").strip()
            out.append({"id": clause_id, "title": title, "line": original_line})
            continue
        
        # 策略2: 在清理后的行中使用宽松匹配
        m = p_num_loose.search(cleaned_line)
        if m:
            num_part = m.group(1)
            # determine suffix from the match text
            full_match = m.group(0)
            suffix = "款" if "款" in full_match else "条"
            clause_id = f"第{num_part}{suffix}"
            title = (m.group(2) or "").strip()
            out.append({"id": clause_id, "title": title, "line": original_line})
            continue

        m = p_cn_loose.search(cleaned_line)
        if m:
            raw_cn = m.group(1)
            n = _cn_to_int(raw_cn)
            if n is not None:
                clause_id = f"第{n}条"
            else:
                clause_id = f"第{raw_cn}条"
            title = (m.group(2) or "").strip()
            out.append({"id": clause_id, "title": title, "line": original_line})
            continue
        
        # 策略3: 在原始行中直接搜索（项目符号清理不彻底的情况）
        m = p_num_loose.search(ln_stripped)
        if m:
            num_part = m.group(1)
            full_match = m.group(0)
            suffix = "款" if "款" in full_match else "条"
            clause_id = f"第{num_part}{suffix}"
            title = (m.group(2) or "").strip()
            out.append({"id": clause_id, "title": title, "line": original_line})
            continue
        
        m = p_cn_loose.search(ln_stripped)
        if m:
            raw_cn = m.group(1)
            n = _cn_to_int(raw_cn)
            if n is not None:
                clause_id = f"第{n}条"
            else:
                clause_id = f"第{raw_cn}条"
            title = (m.group(2) or "").strip()
            out.append({"id": clause_id, "title": title, "line": original_line})
            continue

    # 去重（按 id）
    seen = set()
    uniq = []
    for it in out:
        if it["id"] in seen:
            continue
        seen.add(it["id"])
        uniq.append(it)
    return uniq

def _looks_like_has_clause_number(loc: str) -> bool:
    """检查 location 是否已包含条款编号（支持数字和中文数字）"""
    if not loc:
        return False
    # 匹配数字格式：第1条、第3.1条、第2.1(2)条/款
    if re.search(r"第\s*[0-9\.\(\)]+\s*[条款]", loc):
        return True
    # 匹配中文数字格式：第一条、第七条
    if re.search(r"第[一二三四五六七八九十百千]+条", loc):
        return True
    return False

def _best_match_clause_id(clause_list: List[Dict[str, str]], hint: str) -> Optional[str]:
    """
    用 title/line 的简单包含匹配，为没有编号的 location 补一个"第X条"。
    改进：支持更灵活的匹配，包括部分匹配和关键词匹配。
    """
    if not clause_list:
        return None
    h = (hint or "").strip()
    if not h:
        return None

    # 策略0: 优先尝试从 hint 中提取数字并精确匹配条款ID（忽略条/款后缀差异）
    # 匹配 hint 中的 "第3.1" 或 "第3.1条"
    m_num = re.search(r"第\s*([0-9\.\(\)]+)", h)
    if m_num:
        target_num = m_num.group(1)
        for c in clause_list:
            cid = c["id"]
            # 提取 clause_id 中的数字
            m_c = re.match(r"第\s*([0-9\.\(\)]+)[条款]", cid)
            if m_c and m_c.group(1) == target_num:
                return cid
    
    # 中文数字匹配
    m_cn = re.search(r"第\s*([一二三四五六七八九十百千]+)\s*[条款]", h)
    if m_cn:
        target_cn = m_cn.group(1)
        target_int = _cn_to_int(target_cn)
        for c in clause_list:
            cid = c["id"]
            m_c = re.match(r"第\s*([0-9]+)[条款]", cid)
            if m_c and target_int is not None and int(m_c.group(1)) == target_int:
                return cid
            # 也尝试匹配中文 ID
            m_c_cn = re.match(r"第\s*([一二三四五六七八九十百千]+)[条款]", cid)
            if m_c_cn and m_c_cn.group(1) == target_cn:
                return cid

    # 提取关键词（去除常见停用词）
    stop_words = {"的", "及", "和", "与", "或", "修订", "新增", "条款", "第", "条", "款"}
    tokens = [t.strip() for t in re.split(r"[\s\+\|\-/，。；：、]+", h) 
              if t.strip() and len(t.strip()) >= 2 and t.strip() not in stop_words]
    
    if not tokens:
        return None

    # 简单打分：命中 title/line 的关键词越多越高
    best = None
    best_score = 0
    
    for c in clause_list:
        title = c.get("title", "").strip()
        line = c.get("line", "").strip()
        score = 0
        
        # 完全匹配标题（最高分）
        if title and h in title:
            score += 20
        if title and title in h:
            score += 15
        
        # 关键词匹配
        matched_tokens = 0
        for token in tokens:
            if len(token) >= 2:
                if token in title:
                    score += 5
                    matched_tokens += 1
                elif token in line:
                    score += 3
                    matched_tokens += 1
        
        # 如果匹配到多个关键词，额外加分
        if matched_tokens >= 2:
            score += 5
        
        if score > best_score:
            best_score = score
            best = c["id"]
    
    # 降低匹配阈值，只要有一定匹配就返回
    return best if best_score >= 3 else None

def _search_clause_in_text(text: str, keyword: str) -> Optional[str]:
    """
    在文档文本中搜索包含关键词的条款编号
    例如：搜索"付款及结算方式"，找到"第3条 付款及结算方式"，返回"第3条"
    改进：支持更灵活的搜索，包括跨行匹配和部分匹配
    """
    if not text or not keyword:
        return None
    
    # 清理关键词，提取核心词
    keyword_clean = keyword.strip()
    if not keyword_clean:
        return None
    
    # 提取关键词中的核心词（去除常见词）
    stop_words = {"的", "及", "和", "与", "或", "修订", "新增", "条款", "第", "条", "责任", "方式"}
    keywords = [kw for kw in re.split(r"[\s、，。；：]+", keyword_clean) 
                if kw and len(kw) >= 2 and kw not in stop_words]
    
    if not keywords:
        keywords = [keyword_clean]
    
    # 搜索模式：第X条 + 关键词（支持多种格式）
    # 模式1: 第X条 关键词（同一行）
    patterns = [
        # 数字格式：第3条 / 第3.1条 / 第3.1(1)款 + 关键词
        re.compile(rf"第\s*([0-9\.\(\)]+)\s*([条款])[^\n]*{re.escape(keyword_clean)}", re.IGNORECASE),
        # 中文数字格式：第三条 + 关键词
        re.compile(rf"第\s*([一二三四五六七八九十百千]+)\s*条[^\n]*{re.escape(keyword_clean)}", re.IGNORECASE),
    ]
    
    # 如果关键词较长，尝试部分匹配
    if len(keyword_clean) > 4:
        for kw in keywords[:3]:  # 只尝试前3个关键词
            if len(kw) >= 2:
                patterns.extend([
                    re.compile(rf"第\s*([0-9\.\(\)]+)\s*([条款])[^\n]*{re.escape(kw)}", re.IGNORECASE),
                    re.compile(rf"第\s*([一二三四五六七八九十百千]+)\s*条[^\n]*{re.escape(kw)}", re.IGNORECASE),
                ])
    
    # 尝试匹配
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            clause_num = match.group(1)
            # 如果是中文数字，转换为阿拉伯数字
            if re.match(r"^[一二三四五六七八九十百千]+$", clause_num):
                num = _cn_to_int(clause_num)
                if num is not None:
                    return f"第{num}条"
            else:
                # 数字格式，获取后缀
                if match.lastindex >= 2:
                    suffix = match.group(2)
                else:
                    suffix = "条" # Default
                return f"第{clause_num}{suffix}"
    
    # 如果精确匹配失败，尝试模糊匹配：查找包含关键词的行，然后向前查找最近的条款编号
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if keyword_clean in line or any(kw in line for kw in keywords if len(kw) >= 2):
            # 向前查找最近的条款编号（最多向前查找10行）
            for j in range(max(0, i-10), i+1):
                search_line = lines[j]
                # 查找条款编号
                m = re.search(r"第\s*([0-9\.\(\)]+)\s*([条款])", search_line)
                if m:
                    return f"第{m.group(1)}{m.group(2)}"
                m = re.search(r"第\s*([一二三四五六七八九十百千]+)\s*条", search_line)
                if m:
                    cn = m.group(1)
                    num = _cn_to_int(cn)
                    if num is not None:
                        return f"第{num}条"
    
    return None


def _ensure_clause_number(payload: Dict[str, Any], clause_list: List[Dict[str, str]], text: str = "") -> Dict[str, Any]:
    """
    强制 risks/revisions/raw_excerpt 的 location 都带 "第X条/第X.Y条…"
    如果模型没给编号，则按关键词匹配补一个"第X条"。
    如果匹配失败，至少设置一个默认值。
    """
    # 获取第一个条款作为兜底
    default_clause = clause_list[0]["id"] if clause_list else None
    
    def fix_loc(obj: Dict[str, Any], hint_fields: List[str]) -> None:
        # 确保 location 字段存在
        if "location" not in obj:
            obj["location"] = ""
        
        loc = (obj.get("location") or "").strip()
        
        # 如果已经有条款编号，直接返回
        if _looks_like_has_clause_number(loc):
            return
        
        # 尝试匹配条款编号
        hint = ""
        for f in hint_fields:
            v = obj.get(f)
            if isinstance(v, str) and v.strip():
                hint += " " + v.strip()
        
        matched = None
        
        # 策略1: 从条款列表中匹配
        if clause_list:
            matched = _best_match_clause_id(clause_list, loc + hint)
        
        # 策略2: 如果条款列表为空或匹配失败，从文档文本中搜索
        if not matched and text and loc:
            # 提取关键词（去除常见词）
            keywords = [loc]
            # 如果 location 包含多个词，尝试每个词
            for word in re.split(r"[\s、，。；：]+", loc):
                word = word.strip()
                if word and len(word) >= 2:
                    keywords.append(word)
            
            for keyword in keywords:
                matched = _search_clause_in_text(text, keyword)
                if matched:
                    break
        
        # 统一规范：location 只保留「编号」本身，不再拼接整段条款内容
        if matched:
            # 直接使用匹配到的编号（如「第3条」「第3.1条」）
            obj["location"] = matched
        elif default_clause:
            # 匹配失败但有默认条款时，使用默认条款编号
            obj["location"] = default_clause
        else:
            # 如果完全没有可用编号，统一使用占位符，避免把整段条款正文当作 location 传给前端
            # 这样前端不会再出现「条款编号和条款内容混在一起」的情况
            obj["location"] = "（未定位条款编号）"

    for r in payload.get("risks", []) or []:
        if isinstance(r, dict):
            fix_loc(r, ["title", "risk", "suggestion"])

    for rv in payload.get("revisions", []) or []:
        if isinstance(rv, dict):
            fix_loc(rv, ["text", "type"])

    for ex in payload.get("raw_excerpt", []) or []:
        if isinstance(ex, dict):
            fix_loc(ex, ["text"])

    return payload

def _allowed_locations(clause_list: List[Dict[str, str]]) -> List[str]:
    return [c.get("id", "") for c in clause_list if isinstance(c, dict) and c.get("id")]

def _extract_location_base(loc: str) -> str:
    s = (loc or "").strip()
    if " / " in s:
        return s.split(" / ", 1)[0].strip()
    return s

def _validate_payload(payload: Dict[str, Any], allowed: List[str]) -> Tuple[bool, List[str]]:
    errors: List[str] = []
    if not isinstance(payload, dict):
        return False, ["PAYLOAD_NOT_DICT"]
    if "summary" not in payload or not isinstance(payload.get("summary"), str):
        errors.append("MISSING_SUMMARY")
    if "risks" not in payload or not isinstance(payload.get("risks"), list):
        errors.append("MISSING_RISKS")
    if "revisions" not in payload or not isinstance(payload.get("revisions"), list):
        errors.append("MISSING_REVISIONS")
    if "raw_excerpt" not in payload or not isinstance(payload.get("raw_excerpt"), list):
        errors.append("MISSING_RAW_EXCERPT")
    risks = payload.get("risks") or []
    revisions = payload.get("revisions") or []
    excerpts = payload.get("raw_excerpt") or []
    allowed_set = set(allowed or [])
    strict_location_checks = bool(allowed_set)
    excerpt_locs = [ _extract_location_base(e.get("location","")) for e in excerpts if isinstance(e, dict) ]
    for i, r in enumerate(risks):
        if not isinstance(r, dict):
            errors.append(f"RISK_{i}_NOT_OBJECT")
            continue
        for k in ["title","location","risk","impact","suggestion"]:
            if not isinstance(r.get(k), str) or not r.get(k, "").strip():
                errors.append(f"RISK_{i}_MISSING_{k.upper()}")
        base = _extract_location_base(r.get("location",""))
        if strict_location_checks:
            if base not in allowed_set:
                errors.append(f"RISK_{i}_LOCATION_NOT_ALLOWED:{base}")
            if base not in excerpt_locs:
                errors.append(f"RISK_{i}_NO_EXCERPT_FOR_LOCATION:{base}")
    for i, rv in enumerate(revisions):
        if not isinstance(rv, dict):
            errors.append(f"REV_{i}_NOT_OBJECT")
            continue
        for k in ["location","type","text"]:
            if not isinstance(rv.get(k), str) or not rv.get(k, "").strip():
                errors.append(f"REV_{i}_MISSING_{k.upper()}")
        typ = rv.get("type","")
        base = _extract_location_base(rv.get("location",""))
        if typ not in ("修订","新增"):
            errors.append(f"REV_{i}_TYPE_INVALID:{typ}")
        if strict_location_checks:
            if base not in allowed_set:
                errors.append(f"REV_{i}_LOCATION_NOT_ALLOWED:{base}")
        if typ == "新增":
            if not isinstance(rv.get("reason"), str) or not rv.get("reason","").strip():
                errors.append(f"REV_{i}_MISSING_REASON")
            if " / " not in (rv.get("location","") or ""):
                errors.append(f"REV_{i}_MISSING_INSERT_POSITION")
            if "**" not in (rv.get("text","") or ""):
                errors.append(f"REV_{i}_MISSING_BOLD_FOR_NEW")
        else:
            if "**" not in (rv.get("text","") or ""):
                errors.append(f"REV_{i}_MISSING_BOLD_FOR_EDIT")
        if strict_location_checks:
            if base not in excerpt_locs:
                errors.append(f"REV_{i}_NO_EXCERPT_FOR_LOCATION:{base}")
    ok = len([e for e in errors if e]) == 0
    return ok, errors

def _local_fix_payload(payload: Dict[str, Any], clause_list: List[Dict[str, str]], text: str) -> Dict[str, Any]:
    allowed = _allowed_locations(clause_list)
    default_clause = clause_list[0]["id"] if clause_list else ""
    clause_map = {c["id"]: c for c in clause_list if isinstance(c, dict) and c.get("id")}
    def try_match(base_hint: str, extra_hint: str) -> str:
        if base_hint in allowed:
            return base_hint
        hint = (base_hint or "") + " " + (extra_hint or "")
        m = _best_match_clause_id(clause_list, hint)
        if m:
            return m
        if text and hint.strip():
            m2 = _search_clause_in_text(text, hint.strip())
            if m2:
                return m2
        return default_clause or base_hint or ""
    ex_list = payload.get("raw_excerpt") or []
    risks = payload.get("risks") or []
    revs = payload.get("revisions") or []
    for r in risks:
        if not isinstance(r, dict):
            continue
        base = _extract_location_base(r.get("location",""))
        matched = try_match(base, " ".join([r.get("title",""), r.get("risk",""), r.get("suggestion","")]))
        r["location"] = matched
        need_excerpt = _extract_location_base(r.get("location","")) not in [ _extract_location_base(e.get("location","")) for e in ex_list if isinstance(e, dict) ]
        if need_excerpt and matched in clause_map:
            ex_list.append({"location": matched, "text": clause_map[matched].get("line","")[:200]})
    for rv in revs:
        if not isinstance(rv, dict):
            continue
        base = _extract_location_base(rv.get("location",""))
        matched = try_match(base, rv.get("text",""))
        typ = (rv.get("type","") or "").strip()
        if typ not in ("修订", "新增"):
            if any(k in typ for k in ("新增", "补充", "增加", "加入")):
                typ = "新增"
            else:
                typ = "修订"
        rv["type"] = typ
        if typ == "新增":
            if "reason" not in rv or not (rv.get("reason") or "").strip():
                rv["reason"] = "根据审查立场补充保护性条款"
            if " / " not in (rv.get("location") or ""):
                rv["location"] = f"{matched} / 建议新增于{matched}之后"
            else:
                rv["location"] = f"{matched} / {rv['location'].split(' / ',1)[1]}"
            if "**" not in (rv.get("text") or ""):
                rv["text"] = f"**{rv.get('text','').strip()}**"
        else:
            rv["location"] = matched
            if not (rv.get("text") or "").strip():
                rv["text"] = "（建议删除该条款或删除不合理内容）"
            if "**" not in (rv.get("text") or ""):
                rv["text"] = f"**{rv.get('text','').strip()}**"
        need_excerpt = _extract_location_base(rv.get("location","")) not in [ _extract_location_base(e.get("location","")) for e in ex_list if isinstance(e, dict) ]
        if need_excerpt and matched in clause_map:
            ex_list.append({"location": matched, "text": clause_map[matched].get("line","")[:200]})
    payload["raw_excerpt"] = ex_list
    return payload

def _repair_payload_with_model(payload: Dict[str, Any], clause_hint: str, params: Dict[str, Any]) -> Dict[str, Any]:
    allowed_locs = []
    for line in (clause_hint or "").splitlines():
        line = line.strip()
        if line.startswith("- "):
            v = line[2:].strip()
            if v:
                v = re.sub(r"（.*?）$", "", v).strip()
                allowed_locs.append(v)
    repair_sys = f"你是校验与修复器。只输出符合规则的纯JSON。必须仅使用以下可用location：{allowed_locs}。严格按参数：{params}。"
    repair_usr = json.dumps(payload, ensure_ascii=False)
    try:
        raw = _call_deepseek_chat(
            [
                {"role": "system", "content": repair_sys},
                {"role": "user", "content": repair_usr},
            ],
            temperature=0.0,
        )
        fixed = _parse_model_json(raw)
        return fixed
    except Exception:
        return payload

# -----------------------------
# API
# -----------------------------
@app.post("/api/reviews")
async def create_review(
    file: UploadFile = File(...),

    # 审查角度：仅保留 PartyA / PartyB / Other
    review_position: str = Form("PartyA"),
    other_role: str = Form(""),

    bargaining_power: str = Form("Strong"),
    review_depth: str = Form("Detailed"),

    follow_minimum_change: str = Form("No"),
    output_pref: str = Form("WithRisks"),
    omission_items: str = Form(""),
    special_instructions: str = Form(""),
):
    debug_structure_enabled = "DEBUG_STRUCTURE" in (special_instructions or "").upper()
    text, debug_structure, preview_text = _read_upload_to_text(file, debug_docx=debug_structure_enabled)
    
    # 调试：打印提取到的原始文本的前几行
    text_lines = text.splitlines()
    print(f"[DEBUG] 提取到的文本总行数: {len(text_lines)}")
    print(f"[DEBUG] 前10行文本内容:")
    for i, line in enumerate(text_lines[:10], 1):
        # 显示原始字符的 Unicode 编码，帮助诊断特殊字符
        line_repr = repr(line[:80])  # repr 会显示特殊字符
        print(f"  行{i}: {line_repr}")
    
    clause_list = _extract_clause_candidates(text)
    
    # 调试：打印提取到的条款列表
    print(f"[DEBUG] 提取到 {len(clause_list)} 个条款:")
    if clause_list:
        for c in clause_list[:15]:  # 打印前15个
            line_preview = c.get('line', '')[:60]
            print(f"  - {c['id']}: {c.get('title', '')} (原始行: {repr(line_preview)})")
    else:
        print(f"  ⚠️  警告：未提取到任何条款！可能原因：")
        print(f"     1. 文档格式不标准（没有'第X条'格式）")
        print(f"     2. 项目符号格式无法识别")
        print(f"     3. 文档内容为空或格式异常")
        print(f"     前5行文本示例:")
        for i, line in enumerate(text_lines[:5], 1):
            print(f"       行{i}: {repr(line[:60])}")

    # 角色映射
    if review_position == "PartyA":
        role_label = "甲方"
    elif review_position == "PartyB":
        role_label = "乙方"
    else:
        role_label = (other_role or "").strip() or "其他方"

    risk_tolerance = "Medium"
    output_preference = "Structured"
    si = special_instructions or ""
    m = re.search(r"(?:RiskTolerance|风险容忍度)\s*[:=]\s*(Low|Medium|High|低|中|高)\b", si, re.I)
    if m:
        v = m.group(1).strip()
        if v in ("低",):
            risk_tolerance = "Low"
        elif v in ("中",):
            risk_tolerance = "Medium"
        elif v in ("高",):
            risk_tolerance = "High"
        else:
            risk_tolerance = v.capitalize()
    m = re.search(
        r"(?:OutputPreference|输出偏好)\s*[:=]\s*(Structured|Narrative|Template-Only|TemplateOnly|结构化|叙事|仅模板|模板)\b",
        si,
        re.I,
    )
    if m:
        v = m.group(1).strip()
        if v in ("仅模板", "模板"):
            output_preference = "Template-Only"
        else:
            output_preference = "Template-Only" if v.lower().replace("_", "-") in ("template-only", "templateonly") else v.capitalize()

    params = {
        "review_position": review_position,
        "role_label": role_label,
        "bargaining_power": bargaining_power,
        "review_depth": review_depth,
        "follow_minimum_change": follow_minimum_change,
        "output_pref": output_pref,
        "risk_tolerance": risk_tolerance,
        "output_preference": output_preference,
        "omission_items": omission_items,
        "special_instructions": special_instructions,
    }

    clause_hint = ""
    if clause_list:
        preview: List[Dict[str, str]] = []
        for c in clause_list:
            if not isinstance(c, dict):
                continue
            cid = (c.get("id") or "").strip()
            if not cid:
                continue
            title = (c.get("title") or "").strip()
            line = (c.get("line") or "").strip()
            preview.append({"id": cid, "title": title, "line": line})
        preview = preview[:60]
        clause_hint = "合同条款编号候选清单（必须从中选择编号写入 location）：\n"
        for c in preview:
            t = f"（{c.get('title')}）" if c.get("title") else ""
            clause_hint += f"- {c.get('id')}{t}\n"

    system_prompt = f"""
你是一名执业律师风格的合同审查智能体，负责对完整合同进行系统化、逐条的专业审查。

审查立场：严格按参数执行：{params}。

硬规则：
- 只能基于合同原文输出，不得编造事实或法条。
- 输出必须为【纯 JSON】（不要 Markdown，不要 ``` 代码块，不要任何多余解释文字）。
- follow_minimum_change = Yes：最小修订（局部增补/替换，不重写整段；尽量保留术语与结构）。
- follow_minimum_change = No：允许更大范围改写，但仍需逐条定位与引用原文依据。

【参数优先级与审查立场】
- special_instructions（特别要求）优先级最高；如与其他参数冲突，必须优先执行，并在 summary 中简要反映执行结果。
- review_position / role_label 决定你站在谁的立场（如甲方/乙方），所有风险判断与修订方向都必须体现这一立场。
- bargaining_power（博弈地位）：Strong 时可以更积极地转移风险；Weak 时应尽量在不破坏整体合作前提下缓释风险。
- risk_tolerance（风险容忍度）仅取：Low / Medium / High，用于控制“哪些风险必须修订、哪些可以接受”。
- output_preference（输出偏好）仅取：Structured / Narrative / Template-Only，用于控制输出风格，但不得改变 JSON 结构。

【风险识别与分级（必须三选一）】
- 对每个被输出的风险点，必须明确标注风险等级为 High / Medium / Low，不得使用“可能”“略有”等模糊表述。
- 将风险等级写入 risks[].title 前缀，例如：[High] 违约责任不对等。

【链式参数规则（优先级最高，命中则覆盖后续执行路径）】
规则1｜强势方＋模板模式
- 条件：bargaining_power = Strong 且 output_preference = Template-Only
- 动作：仅输出 revisions 与 raw_excerpt；risks 置空；summary 仍需非空，简要说明“按模板模式仅输出可直接用作条款的修订文本”。

规则2｜弱势方＋最小修订
- 条件：bargaining_power = Weak 且 follow_minimum_change = Yes
- 动作：仅做微幅修订；避免结构性调整；不可避免风险仅提示与缓释建议，不要求对方改条款；严禁把整条条款改成对己方过于有利的极端版本。

规则3｜高容忍＋粗略审查
- 条件：risk_tolerance = High 且 review_depth = Lite
- 动作：仅对 High 风险输出 revisions；Medium 风险仅在 risks 中提示可接受的风险点（可不输出 revisions）；Low 风险直接跳过不输出。

规则4｜低容忍＋详细审查
- 条件：risk_tolerance = Low 且 review_depth = Detailed
- 动作：High 与 Medium 风险必须给出 revisions；Low 风险至少在 risks 中提示（可不输出 revisions），避免遗漏潜在问题。

【决策分支规则（未命中链式规则时使用；每条款仅触发其一）】
分支A｜详细审查 + 高/中风险
- 条件：review_depth = Detailed 且（High 风险；或 Medium 风险且 risk_tolerance = Low）
- 动作：输出 risks + revisions，并在 suggestion/reason 中给出清晰、可操作的修改方向与 2–3 条谈判理由（简明表达）。

分支B｜粗略审查 + 可忽略风险
- 条件：review_depth = Lite 且（Low 风险；或 Medium 风险且 risk_tolerance = High）
- 动作：该条款完全跳过（不输出任何对应的 risks/revisions/raw_excerpt 项），以减少无关噪音。

分支C｜弱势方 + 不可避免风险
- 条件：bargaining_power = Weak 且风险为不可避免（无法通过修订实质解决）
- 动作：输出 risks（说明风险与后果，并给出缓释建议或注意事项）；不要求对方修订；可不输出 revisions。

分支D｜强势方
- 条件：bargaining_power = Strong
- 动作：输出 risks + revisions，以最大化将风险转移给对方或强化己方保护，同时保持商业上可接受。

【逐条审查步骤（用于你的内部思考，不单独输出）】
- 步骤1：识别本条款中与审查立场相关的关键问题点（哪一句、哪一段、对谁不利）。
- 步骤2：用专业律师语言说明该问题的法律/商业风险（写入 risks[].risk）。
- 步骤3：提出清晰的修改方向（写入 risks[].suggestion），说明应如何改、改到什么程度。
- 步骤4：生成修订后的条款全文（写入 revisions[].text），仅保留最终建议版本，变更部分用 **加粗** 标记。
- 步骤5：给出 1–3 条可用于谈判的理由（简化后写入 revisions[].reason 或浓缩到 risks[].impact 中）。

【禁止事项】
- 禁止合并多条互不相干的条款一起审查，每条风险/修订的 location 必须指向单一条款编号。
- 禁止只给出空泛评论而不提供具体可执行的修改方向。
- 禁止在 revisions[].text 中重复长篇法律分析或背景说明，这些内容应放在 risks[].risk / risks[].impact 中。

【条款定位规则（必须严格遵守）】
你会收到一份“编号索引/提示”（clause_hint），其中列出了【可用的 location 值】（来自合同原文或后端规范化后的文本）。
- risks[].location / revisions[].location / raw_excerpt[].location 必须从 clause_hint 列表中选择，禁止输出 clause_hint 列表之外的编号。
- 若某风险/修订无法在 clause_hint 中找到精确 location：必须选择“最接近的上级条款 location”，并在 raw_excerpt.text 中引用能证明其属于该条款的原文片段（包含关键句）。
- 禁止空 location；禁止使用“第X条”这种泛化定位（除非 clause_hint 里只有“第X条”）。
- 若涉及条款的更细层级（如第2条第1款(2)）但 clause_hint 未提供该层级编号：不得臆造 2.1(2)。应返回上级 location，并在 raw_excerpt 中把(2)那段原文完整摘录出来。

【新增保护性条款规则】
在审查过程中，你必须额外判断：
- 合同是否缺失对审查立场一方（如甲方/乙方）的关键保护性条款。

仅在满足以下条件时，才允许提出“新增条款”：
1. 原合同中不存在同类保护条款；
2. 该新增条款在同类商业合同中属于常见或合理安排。

新增条款必须：
- 明确标注为 type = "新增"
- 给出【建议插入位置】（如“建议新增为第X条，置于违约责任条款之后”）
- 条款全文整体使用 **加粗**
- 不得与原合同已有条款冲突或重复

【新增条款触发清单】
当出现以下情形之一时，应主动考虑是否需要新增保护性条款：
- 合同未约定提前无责解除权；
- 未约定知识产权归属或侵权责任；
- 未约定违约金上限/下限；
- 未约定赔偿范围或排除间接损失；
- 未约定不可抗力后的处理机制；
- 未约定合规、制裁、出口管制等条款（涉外/技术类合同）；
- 未约定保险、担保或履约保障；
- 未约定争议解决方式或管辖不利于审查立场一方。

【条款遗漏审查（基于 omission_items）】
- omission_items 可能包含用户特别关心、需要逐项排查是否缺失的条款项目列表，可以按换行、分号、顿号等拆分为多项。
- 当 omission_items 为空或只包含空白时，可以不进行条款遗漏审查（但仍需保证 JSON 中存在字段 omission_review，可为空字符串）。
- 当 omission_items 非空时，你必须对每一项执行以下逻辑：
  - 在合同全文中检索与该项目高度相关的条款；
  - 若完全缺失该类条款：
    - 必须指出建议新增于明确的数字条款编号位置，例如：“新增为第X条”或“新增于第X条之后设立第X+1条”；
    - 必须提供新增条款全文，新增内容全部加粗；
  - 若已存在相关条款：
    - 必须指出原条款的明确数字编号，例如：“第X条”或“第X.X条”；
    - 并在该条款基础上进行最小必要修订，所有修改处加粗。
- 条款遗漏审查的结果需要汇总为一个字符串写入 JSON 字段 omission_review；如果无检查项则写空字符串。
- 当 omission_items 非空时，omission_review 字段必须为【非空字符串】，不得缺失、不得为 null。
- 对每一个检查项的输出文本，必须严格遵守以下固定结构（按顺序输出，原样使用括号与序号）：
  （一）条款编号：N（当检查结果为“否”时，这里必须写“Null”；当检查结果为“是”时，这里必须写明对应的数字条款编号，如“第X条”“第X.X条”）
  （二）检查结果：是否存在该条款（是/否）
  • 原条款编号：第X条 / 第X.X条（仅当检查结果为“是”时输出）
  • 修订后条款（修改处加粗）：（仅当检查结果为“是”时输出）
  • 建议新增位置：例如“新增为第X条”或“新增于第X条之后设立第X+1条”（仅当检查结果为“否”时输出）
  • 新增条款全文（新增处加粗）：（仅当检查结果为“否”时输出）
  （三）风险点与修订理由
- N 可以按检查项顺序从1开始编号。

【修订输出规则（必须严格遵守）】
- revisions[].text 只写“修订后条款最终版本”，不要把“建议/理由/解释”混在 text 里。
- 凡是【发生变更的文字】必须用 **加粗**。
- 若 type = "新增"：新增条款全文必须整体 **加粗**（整段加粗）。
- revisions[].type 只能是 "修订" 或 "新增"；如你认为应“删除”，也必须写为 type="修订"，并在 text 写明“（删除该条款/删除某段内容）”且用 **加粗**。

【原文摘录规则】
- raw_excerpt 只返回与 risks/revisions 对应的原文片段；不要全文。
- 每个 raw_excerpt 必须能对应该条风险或修订（同一个 location 可以多条）。

{clause_hint}

输出 JSON 结构如下（字段名必须一致）：
{{
  "summary": "一句话概括",
  "omission_review": "条款遗漏审查汇总结果字符串；当 omission_items 为空时设为 \"\"，当 omission_items 非空时必须为非空字符串，包含按固定结构输出的所有检查项",
  "risks": [
    {{
      "title": "风险点标题",
      "location": "必须取自 clause_hint",
      "risk": "风险说明",
      "impact": "可能后果",
      "suggestion": "修改方向"
    }}
  ],
  "revisions": [
    {{
      "location": "必须取自 clause_hint / 若为新增需包含“建议新增于第X条之后”等插入建议",
      "type": "修订/新增",
      "text": "修订后条款最终版本（变更处**加粗**；新增则全文**加粗**）",
      "reason": "为什么需要新增该条款（一句话）"
    }}
  ],
  "raw_excerpt": [
    {{
      "location": "必须取自 clause_hint",
      "text": "与风险/修订对应的原文片段"
    }}
  ]
}}

【风险提示与修订建议的一致性要求】
- 在不违反分支/链式规则的前提下，你必须尽量保证：每一个 risks[i] 所描述的 High/Medium 风险，都有至少一条 revisions[j] 与之对应：
  - 要求 revisions[j].location 与 risks[i].location 完全一致；
  - revisions[j].text 中的修订建议，应能直接缓解或解决该风险。
- 同样地，对于每一条 revisions[k]，如有对应风险原因，应在 risks 中给出说明：
  - 若确实只是“纯新增且无明显风险背景”，可以不在 risks 中重复描述。
- 若命中分支B（跳过）或分支C（不可避免风险）或 output_preference = Template-Only，则允许 risks 与 revisions 不完全一一对应，但 summary 必须解释执行结果与原因。
"""

    user_prompt = f"合同原文如下：\n\n{text}"

    try:
        raw_out = _call_deepseek_chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
        )
        payload = _parse_model_json(raw_out)
        payload = _normalize_payload(payload)
        
        # 新增：为 excerpt 分配 id，扩展 excerpt 上下文长度，生成 anchor 与绑定关系
        _assign_excerpt_ids(payload)
        _expand_excerpt_context(payload, text)
        _attach_anchors(payload)
        
        # 调试：打印 AI 返回的原始数据
        print(f"[DEBUG] AI 返回的风险数量: {len(payload.get('risks', []))}")
        print(f"[DEBUG] AI 返回的修订数量: {len(payload.get('revisions', []))}")
        print(f"[DEBUG] 提取到的条款数量: {len(clause_list)}")
        if payload.get('risks'):
            for i, r in enumerate(payload['risks'][:5]):
                print(f"  [DEBUG] 风险 {i+1} location (处理前): {repr(r.get('location', 'N/A'))}")
                print(f"  [DEBUG] 风险 {i+1} title: {r.get('title', 'N/A')[:50]}")

        # 兜底：如果模型没写编号，后端自动补"第X条 …"
        # 传入原始文本，以便在条款列表为空时也能搜索
        payload = _ensure_clause_number(payload, clause_list, text)
        allowed = _allowed_locations(clause_list)
        ok, errs = _validate_payload(payload, allowed)
        if not ok:
            print(f"[DEBUG] 校验失败，尝试本地修复: {errs[:10]}")
            payload = _local_fix_payload(payload, clause_list, text)
            # 本地修复后重新绑定 anchors
            _assign_excerpt_ids(payload)
            _expand_excerpt_context(payload, text)
            _attach_anchors(payload)
            ok2, errs2 = _validate_payload(payload, allowed)
            if not ok2:
                print(f"[DEBUG] 本地修复仍失败，调用模型修复: {errs2[:10]}")
                payload = _repair_payload_with_model(payload, clause_hint, params)
                payload = _normalize_payload(payload)
                payload = _ensure_clause_number(payload, clause_list, text)
                _assign_excerpt_ids(payload)
                _expand_excerpt_context(payload, text)
                _attach_anchors(payload)
                ok3, errs3 = _validate_payload(payload, allowed)
                if not ok3:
                    payload["error"] = f"VALIDATION_FAILED: {', '.join(errs3[:12])}"

        if debug_structure_enabled and debug_structure:
            payload["debug_structure"] = debug_structure
        # 预览区全文文本：始终使用未自动加“第X条”的原始文本
        payload["document_text"] = preview_text or text
        return payload

    except HTTPException:
        raise
    except Exception as e:
        msg = str(e)
        if "RESOURCE_EXHAUSTED" in msg or "Quota" in msg or "quota" in msg:
            raise HTTPException(status_code=429, detail=f"LLM_QUOTA_EXCEEDED: {msg}")
        raise HTTPException(status_code=500, detail=f"LLM_CALL_FAILED: {msg}")
