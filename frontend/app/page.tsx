"use client";

import React, { useMemo, useState, useEffect } from "react";

type Risk = {
  title?: string;
  location?: string;
  risk?: string;
  impact?: string;
  suggestion?: string;
  anchor_text?: string;
  excerpt_ids?: string[];
};

type Revision = {
  location?: string;
  type?: string;
  text?: string;
  anchor_text?: string;
  excerpt_ids?: string[];
};

type Excerpt = {
  location?: string;
  text?: string;
  id?: string;
};

type ResultPayload = {
  summary?: string;
  omission_review?: string;
  risks?: Risk[];
  revisions?: Revision[];
  raw_excerpt?: Excerpt[];
  debug_structure?: string;
  error?: string;
  raw?: string;
  document_text?: string;
};

function renderBold(text: string) {
  const parts = (text || "").split(/(\*\*[^*]+\*\*)/g);
  return parts.map((p, i) => {
    const m = p.match(/^\*\*([^*]+)\*\*$/);
    if (m) return <strong key={i} className="font-semibold">{m[1]}</strong>;
    return <span key={i}>{p}</span>;
  });
}

// 改进的对比函数：使用更智能的算法，加粗显示不一致部分（修订、新增）
function smartCompareRevision(revisionText: string, originalText: string): React.ReactNode[] {
  if (!revisionText) return [];
  if (!originalText) {
    // 如果没有原条款，整个修订文本都加粗（视为新增）
    return [<strong key="all-new" className="font-bold">{revisionText}</strong>];
  }
  
  const rev = revisionText.trim();
  const orig = originalText.trim();
  
  // 如果修订文本已经包含 **加粗** 标记，优先使用
  if (rev.includes('**')) {
    return [renderBold(rev)];
  }
  
  // 如果完全相同，直接返回
  if (rev === orig) {
    return [<span key="same">{rev}</span>];
  }
  
  // 使用字符级别的差异检测
  const revChars = Array.from(rev);
  const origChars = Array.from(orig);
  const result: React.ReactNode[] = [];
  
  // 使用简单的字符级对比，找出差异
  let revIdx = 0;
  let origIdx = 0;
  let currentDiff = '';
  let currentMatch = '';
  
  while (revIdx < revChars.length || origIdx < origChars.length) {
    if (revIdx < revChars.length && origIdx < origChars.length && revChars[revIdx] === origChars[origIdx]) {
      // 匹配的字符
      if (currentDiff) {
        // 先输出之前的差异部分（加粗）
        result.push(<strong key={`diff-${revIdx}`} className="font-bold">{currentDiff}</strong>);
        currentDiff = '';
      }
      currentMatch += revChars[revIdx];
      revIdx++;
      origIdx++;
    } else if (revIdx < revChars.length) {
      // 修订文本中新增或修改的字符
      if (currentMatch) {
        result.push(<span key={`match-${revIdx}`}>{currentMatch}</span>);
        currentMatch = '';
      }
      // 尝试在原文中查找当前字符
      let found = false;
      for (let k = origIdx; k < origChars.length && k < origIdx + 5; k++) {
        if (revChars[revIdx] === origChars[k]) {
          found = true;
          break;
        }
      }
      if (!found) {
        currentDiff += revChars[revIdx];
      } else {
        if (currentDiff) {
          result.push(<strong key={`diff-${revIdx}`} className="font-bold">{currentDiff}</strong>);
          currentDiff = '';
        }
        currentMatch += revChars[revIdx];
      }
      revIdx++;
      if (!found && origIdx < origChars.length) {
        origIdx++; // 跳过原文中不匹配的字符
      }
    } else {
      // 原文中有但修订文本中删除的字符（跳过）
      if (currentMatch) {
        result.push(<span key={`match-${revIdx}`}>{currentMatch}</span>);
        currentMatch = '';
      }
      origIdx++;
    }
  }
  
  // 处理剩余的差异和匹配部分
  if (currentDiff) {
    result.push(<strong key="diff-final" className="font-bold">{currentDiff}</strong>);
  }
  if (currentMatch) {
    result.push(<span key="match-final">{currentMatch}</span>);
  }
  
  return result.length > 0 ? result : [<strong key="fallback" className="font-bold">{rev}</strong>];
}

function extractClauseNumber(loc?: string): string {
  const s = (loc || "").trim();
  if (!s) return "其他";
  
  // 统一归一化：仅保留顶层“第X条”
  // 支持数字：第X条 / 第X款 / 第X.Y条 / 第X.Y(2)款
  const mNum = s.match(/第\s*([0-9\.\(\)]+)\s*[条款]/);
  if (mNum) {
    // 只取顶层数字（点号/括号前的第一段）
    const raw = mNum[1];
    const top = String(raw).split(/[.\(]/)[0];
    return `第${top}条`;
  }
  // 支持中文数字：第一条 / 第六款
  const mCn = s.match(/第\s*([一二三四五六七八九十百千]+)\s*[条款]/);
  if (mCn) {
    const cn = mCn[1];
    // 简易中文数字到阿拉伯数字的映射（与后端保持一致逻辑）
    const map: Record<string, number> = { "零":0,"一":1,"二":2,"三":3,"四":4,"五":5,"六":6,"七":7,"八":8,"九":9,"十":10 };
    let val = 0;
    if (cn.length === 1 && map[cn] !== undefined) {
      val = map[cn];
    } else {
      // 粗略转换：仅支持到“十X”形式（如“十一”“十二”）
      if (cn.startsWith("十")) {
        const tail = cn.slice(1);
        val = 10 + (map[tail] || 0);
      } else {
        val = map[cn] || 0;
      }
    }
    return `第${val || 1}条`;
  }
  
  return s.length > 20 ? s.substring(0, 20) + "..." : s;
}

function locationBase(loc?: string): string {
  return (loc || "").split(" / ")[0].trim();
}

function extractSubClauseIds(text: string | undefined): string[] {
  const base = locationBase(text);
  const mainClause = extractClauseNumber(text);
  if (!base) return [mainClause];

  // Regex to find all clause and sub-clause references, e.g., "第1.2条", "第3.4(1)款"
  const rawMatches = Array.from(base.matchAll(/第\s*([0-9]+(?:\.[0-9]+)*(?:\([0-9]+\))?)\s*([条款])/g));
  const ids = rawMatches
    .map((m) => {
      const num = m[1];
      const suffix = m[2];
      return `第${num}${suffix}`;
    })
    .filter(Boolean);

  if (ids.length === 0) return [mainClause];

  // From the found IDs, we prefer the most specific ones (sub-clauses).
  // If we find any IDs with '.' or '(', we assume they are sub-clauses and only return those.
  const hasSub = ids.some((id) => id.includes(".") || id.includes("("));
  const refined = hasSub ? ids.filter((id) => id.includes(".") || id.includes("(")) : ids;
  
  const finalIds = Array.from(new Set(refined));
  
  // If after filtering we have nothing, fall back to the main clause ID.
  return finalIds.length > 0 ? finalIds : [mainClause];
}

function splitTextByClauseRef(text: string | undefined): Map<string, string> {
  const s = (text || "").trim();
  const out = new Map<string, string>();
  if (!s) return out;

  const matches = Array.from(s.matchAll(/第\s*([0-9]+(?:\.[0-9]+)+(?:\([0-9]+\))?)\s*([条款])/g));
  if (matches.length < 2) return out;

  for (let i = 0; i < matches.length; i++) {
    const cur = matches[i];
    const next = matches[i + 1];
    const start = cur.index ?? 0;
    const end = next?.index ?? s.length;
    const num = cur[1];
    const suffix = cur[2];
    const key = `第${num}${suffix}`;
    const seg = s.slice(start, end).trim();
    if (seg) out.set(key, seg);
  }
  return out;
}

function parseClauseIdToArray(id: string): number[] {
  const m = id.match(/第\s*([0-9\.\(\)]+)/);
  if (!m) return [Number.MAX_SAFE_INTEGER];
  const nums = Array.from(m[1].matchAll(/\d+/g)).map((x) => parseInt(x[0], 10) || 0);
  return nums.length > 0 ? nums : [Number.MAX_SAFE_INTEGER];
}

function compareClauseId(a: string, b: string): number {
  const pa = parseClauseIdToArray(a);
  const pb = parseClauseIdToArray(b);
  const len = Math.max(pa.length, pb.length);
  for (let i = 0; i < len; i++) {
    const va = pa[i] ?? 0;
    const vb = pb[i] ?? 0;
    if (va !== vb) return va - vb;
  }
  return 0;
}

// 按条款分组结果
function groupByClause(result: ResultPayload) {
  const clauseMap = new Map<string, {
    risks: Risk[];
    revisions: Revision[];
    excerpts: Excerpt[];
  }>();
  
  // 处理风险
  (result.risks || []).forEach(risk => {
    const clauseId = extractClauseNumber(risk.location);
    if (!clauseMap.has(clauseId)) {
      clauseMap.set(clauseId, { risks: [], revisions: [], excerpts: [] });
    }
    clauseMap.get(clauseId)!.risks.push(risk);
  });
  
  // 处理修订
  (result.revisions || []).forEach(rev => {
    const clauseId = extractClauseNumber(rev.location);
    if (!clauseMap.has(clauseId)) {
      clauseMap.set(clauseId, { risks: [], revisions: [], excerpts: [] });
    }
    clauseMap.get(clauseId)!.revisions.push(rev);
  });
  
  // 处理原文节选
  (result.raw_excerpt || []).forEach(ex => {
    const clauseId = extractClauseNumber(ex.location);
    if (!clauseMap.has(clauseId)) {
      clauseMap.set(clauseId, { risks: [], revisions: [], excerpts: [] });
    }
    clauseMap.get(clauseId)!.excerpts.push(ex);
  });
  
  return clauseMap;
}

export default function Page() {
  const [page, setPage] = useState<"home" | "results">("home");
  
  const [file, setFile] = useState<File | null>(null);
  const [reviewPosition, setReviewPosition] = useState<"PartyA" | "PartyB" | "Other">("PartyA");
  const [otherRole, setOtherRole] = useState("");
  const [bargainingPower, setBargainingPower] = useState<"Strong" | "Weak">("Strong");
  const [reviewDepth, setReviewDepth] = useState<"Detailed" | "Lite">("Detailed");
  const [followMinChange, setFollowMinChange] = useState<"Yes" | "No">("No");
  const [omissionItems, setOmissionItems] = useState("");
  const [specialInstructions, setSpecialInstructions] = useState("");

  const [showRisks, setShowRisks] = useState(true);
  const [showRevisions, setShowRevisions] = useState(true);
  const [showOmissionCheck, setShowOmissionCheck] = useState(false);
  const [selectedClause, setSelectedClause] = useState<string | null>(null);
  const [collapsedSubClauses, setCollapsedSubClauses] = useState<Record<string, boolean>>({});

  const [loading, setLoading] = useState(false);
  const [errText, setErrText] = useState("");
  const [result, setResult] = useState<ResultPayload | null>(null);
  const [activeHighlight, setActiveHighlight] = useState<{ paragraphIndex: number; text: string } | null>(null);
  

  const roleLabel = useMemo(() => {
    if (reviewPosition === "PartyA") return "甲方";
    if (reviewPosition === "PartyB") return "乙方";
    return otherRole.trim() || "其他";
  }, [reviewPosition, otherRole]);
  
  const clauseMap = useMemo(() => {
    if (!result) return new Map<string, { risks: Risk[]; revisions: Revision[]; excerpts: Excerpt[] }>();
    return groupByClause(result);
  }, [result]);
  
  const clauseList = useMemo(() => {
    return Array.from(clauseMap.keys()).sort((a, b) => {
      const ax = a.match(/第\s*(\d+)\s*条/);
      const bx = b.match(/第\s*(\d+)\s*条/);
      if (ax && bx) {
        return Number(ax[1]) - Number(bx[1]);
      }
      return a.localeCompare(b);
    });
  }, [clauseMap]);



  const subClauseItems = useMemo(() => {
    if (!result) return [];

    const subClauseMap = new Map<string, { risks: Risk[]; revisions: Revision[]; excerpts: Excerpt[] }>();

    function addToSubClause(
      subClauseId: string,
      item: Risk | Revision | Excerpt,
      type: "risk" | "revision" | "excerpt"
    ) {
      if (!subClauseMap.has(subClauseId)) {
        subClauseMap.set(subClauseId, { risks: [], revisions: [], excerpts: [] });
      }
      const group = subClauseMap.get(subClauseId)!;
      if (type === "risk") {
        group.risks.push(item as Risk);
      } else if (type === "revision") {
        group.revisions.push(item as Revision);
      } else {
        group.excerpts.push(item as Excerpt);
      }
    }

    (result.risks || []).forEach((risk) => {
      const subClauseIds = extractSubClauseIds(risk.location);
      subClauseIds.forEach((id) => addToSubClause(id, risk, "risk"));
    });

    (result.revisions || []).forEach((revision) => {
      const subClauseIds = extractSubClauseIds(revision.location);
      subClauseIds.forEach((id) => addToSubClause(id, revision, "revision"));
    });

    (result.raw_excerpt || []).forEach((excerpt) => {
      const subClauseIds = extractSubClauseIds(excerpt.location);
      subClauseIds.forEach((id) => addToSubClause(id, excerpt, "excerpt"));
    });

    return Array.from(subClauseMap.entries())
      .map(([subClauseId, { risks, revisions, excerpts }]) => ({ subClauseId, risks, revisions, excerpts }))
      .sort((a, b) => compareClauseId(a.subClauseId, b.subClauseId));
  }, [result]);

  

  async function onSubmit() {
    setErrText("");
    setResult(null);

    if (!file) {
      setErrText("请先选择文件");
      return;
    }
    if (reviewPosition === "Other" && !otherRole.trim()) {
      setErrText('已选择"其他"，请填写角色名称（例如：丙方/承租方/服务方等）');
      return;
    }

    const form = new FormData();
    form.append("file", file);
    form.append("review_position", reviewPosition);
    form.append("other_role", otherRole.trim());
    form.append("bargaining_power", bargainingPower);
    form.append("review_depth", reviewDepth);
    form.append("follow_minimum_change", followMinChange);
    form.append("output_pref", "WithRisks");
    form.append("omission_items", omissionItems);
    form.append("special_instructions", specialInstructions);

    setLoading(true);
    try {
      const res = await fetch("/api/reviews", { method: "POST", body: form });

      let data: unknown = null;
      try {
        data = await res.json();
      } catch {
        data = null;
      }

      if (!res.ok) {
        const detail =
          (data &&
            typeof data === "object" &&
            ("detail" in data
              ? (data as { detail: string }).detail
              : "error" in data
              ? (data as { error: string }).error
              : undefined)) ||
          `HTTP ${res.status}`;
        setErrText(`请求失败：${detail}`);
        setResult(null);
        return;
      }

      const payload = data as ResultPayload;
      setResult(payload);
      if (payload?.error) {
        setErrText(`error: ${payload.error}`);
      } else {
        // 成功则跳转到结果页
        setPage("results");
        if (clauseList.length > 0) {
          setSelectedClause(clauseList[0]);
        }
      }
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : String(e);
      setErrText(`请求失败：${message}`);
    } finally {
      setLoading(false);
    }
  }

  // 预览区段落与定位
  const previewParagraphs = useMemo(() => {
    const full = result?.document_text || "";
    if (!full) return [];
    return full.split('\n');
  }, [result?.document_text]);

  const previewRefs = useMemo(() => {
    return previewParagraphs.map(() => React.createRef<HTMLDivElement>());
  }, [previewParagraphs]);

  function normalizeSpaces(s: string): string {
    return (s || "").replace(/\s+/g, " ").trim();
  }

  function stripClausePrefix(s: string): string {
    return (s || "").replace(/^第\s*[0-9一二三四五六七八九十百千\.、\(\)]+[条款]\s*/g, "").trim();
  }

  function escapeRegExp(s: string): string {
    return (s || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  function clauseIdToSearchTokens(clauseId: string | undefined): string[] {
    const s = (clauseId || "").trim();
    if (!s) return [];
    const m = s.match(/第\s*([0-9]+(?:\.[0-9]+)*(?:\([0-9]+\))?)\s*[条款]/);
    if (!m) return [];
    const num = m[1];
    const tokens = [num, num.replace(/\((\d+)\)/g, "（$1）")];
    return Array.from(new Set(tokens)).filter(Boolean);
  }

  function findByClauseId(clauseId: string | undefined): { idx: number; match: string } | null {
    const tokens = clauseIdToSearchTokens(clauseId);
    if (tokens.length === 0) return null;
    for (let i = 0; i < previewParagraphs.length; i++) {
      const p = previewParagraphs[i] || "";
      const trimmed = p.trimStart();
      for (const token of tokens) {
        const re = new RegExp(
          `^\\s*(?:第\\s*)?${escapeRegExp(token)}\\s*(?:[、\\.．\\)]|）|\\s)`
        );
        const m = trimmed.match(re);
        if (m) {
          return { idx: i, match: m[0].trim() || token };
        }
      }
    }
    return null;
  }

  function extractKeywords(s: string): string[] {
    const raw = (s || "").replace(/[，。、“”‘’；：,.!?;:\n]/g, " ");
    const tokens = raw.split(/\s+/).filter(t => t && t.length >= 4);
    return tokens.sort((a, b) => b.length - a.length).slice(0, 3);
  }

  function findAnchorInParagraphs(anchor: string, fallbacks: string[]): { idx: number; match: string } | null {
    const normAnchor = normalizeSpaces(anchor);
    const core = stripClausePrefix(anchor);
    const normCore = normalizeSpaces(core);
    if (!normAnchor && !normCore) return null;
    for (let i = 0; i < previewParagraphs.length; i++) {
      const p = previewParagraphs[i];
      const np = normalizeSpaces(p);
      if (
        (anchor && (p.includes(anchor) || np.includes(normAnchor))) ||
        (core && (p.includes(core) || np.includes(normCore)))
      ) {
        return { idx: i, match: core || anchor };
      }
    }
    const base = core || anchor;
    const prefix = base.slice(0, Math.min(60, base.length));
    if (prefix.trim().length >= 10) {
      for (let i = 0; i < previewParagraphs.length; i++) {
        const p = previewParagraphs[i];
        if (p.includes(prefix) || normalizeSpaces(p).includes(normalizeSpaces(prefix))) {
          return { idx: i, match: prefix };
        }
      }
    }
    for (const kw of fallbacks) {
      for (let i = 0; i < previewParagraphs.length; i++) {
        if (previewParagraphs[i].includes(kw)) {
          return { idx: i, match: kw };
        }
      }
    }
    return null;
  }

  function locateByAnchor(anchor: string, extraSource?: string, clauseId?: string) {
    const clauseHit = findByClauseId(clauseId);
    if (clauseHit) {
      setActiveHighlight({ paragraphIndex: clauseHit.idx, text: clauseHit.match });
      const ref = previewRefs[clauseHit.idx]?.current;
      if (ref) {
        ref.scrollIntoView({ behavior: "smooth", block: "center" });
      }
      return;
    }
    const fallbacks = extractKeywords(extraSource || "");
    const hit = findAnchorInParagraphs(anchor, fallbacks);
    if (hit) {
      setActiveHighlight({ paragraphIndex: hit.idx, text: hit.match });
      const ref = previewRefs[hit.idx]?.current;
      if (ref) {
        ref.scrollIntoView({ behavior: "smooth", block: "center" });
      }
    }
  }

  function clearHighlight() {
    setActiveHighlight(null);
  }

  function renderWithHighlight(par: string, idx: number): React.ReactNode {
    if (!activeHighlight || activeHighlight.paragraphIndex !== idx) return <span>{par}</span>;
    return (
      <span className="bg-yellow-600/30 rounded px-1 py-0.5 inline-block whitespace-pre-wrap">
        {par}
      </span>
    );
  }

  // 首页
  if (page === "home") {
    return (
      <main className="min-h-screen bg-gray-900 text-gray-100">
        <div className="mx-auto max-w-4xl px-6 py-12">
          <h1 className="text-4xl font-bold mb-8 text-center text-white">AI合同审查系统</h1>
          
          {errText && (
            <div className="mb-6 bg-red-900/90 border-2 border-red-600 rounded-xl p-4 text-red-200 text-sm shadow-xl">
              {errText}
            </div>
          )}
          
          <div className="space-y-6">
            {/* 审查角度 */}
            <div className="card card-hover">
              <label className="block text-sm font-medium mb-3 text-white">审查角度</label>
              <div className="flex gap-4 flex-wrap items-center">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="radio"
                    name="reviewPosition"
                    value="PartyA"
                    checked={reviewPosition === "PartyA"}
                    onChange={() => setReviewPosition("PartyA")}
                    className="w-4 h-4"
                  />
                  <span>甲方</span>
                </label>
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="radio"
                    name="reviewPosition"
                    value="PartyB"
                    checked={reviewPosition === "PartyB"}
                    onChange={() => setReviewPosition("PartyB")}
                    className="w-4 h-4"
                  />
                  <span>乙方</span>
                </label>
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="radio"
                    name="reviewPosition"
                    value="Other"
                    checked={reviewPosition === "Other"}
                    onChange={() => setReviewPosition("Other")}
                    className="w-4 h-4"
                  />
                  <span>其他</span>
                </label>
                {reviewPosition === "Other" && (
                  <input
                    type="text"
                    placeholder="请输入角色名称"
                    value={otherRole}
                    onChange={(e) => setOtherRole(e.target.value)}
                    className="ml-2 px-3 py-1 bg-gray-700 border border-gray-600 rounded text-sm text-white focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                  />
                )}
              </div>
            </div>
            
            {/* 博弈地位 */}
            <div className="card card-hover">
              <label className="block text-sm font-medium mb-3 text-white">博弈地位</label>
              <div className="flex gap-4">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="radio"
                    name="bargainingPower"
                    value="Strong"
                    checked={bargainingPower === "Strong"}
                    onChange={() => setBargainingPower("Strong")}
                    className="w-4 h-4"
                  />
                  <span>强势</span>
                </label>
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="radio"
                    name="bargainingPower"
                    value="Weak"
                    checked={bargainingPower === "Weak"}
                    onChange={() => setBargainingPower("Weak")}
                    className="w-4 h-4"
                  />
                  <span>弱势</span>
                </label>
              </div>
            </div>
            
            {/* 审查强度 */}
            <div className="card card-hover">
              <label className="block text-sm font-medium mb-3 text-white">审查强度</label>
              <div className="flex gap-4">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="radio"
                    name="reviewDepth"
                    value="Detailed"
                    checked={reviewDepth === "Detailed"}
                    onChange={() => setReviewDepth("Detailed")}
                    className="w-4 h-4"
                  />
                  <span>详细</span>
                </label>
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="radio"
                    name="reviewDepth"
                    value="Lite"
                    checked={reviewDepth === "Lite"}
                    onChange={() => setReviewDepth("Lite")}
                    className="w-4 h-4"
                  />
                  <span>粗略</span>
                </label>
              </div>
            </div>
            
            {/* 是否遵循最小修订原则 */}
            <div className="card card-hover">
              <label className="block text-sm font-medium mb-3 text-white">是否遵循最小修订原则</label>
              <div className="flex gap-4">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="radio"
                    name="followMinChange"
                    value="Yes"
                    checked={followMinChange === "Yes"}
                    onChange={() => setFollowMinChange("Yes")}
                    className="w-4 h-4"
                  />
                  <span>是</span>
                </label>
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="radio"
                    name="followMinChange"
                    value="No"
                    checked={followMinChange === "No"}
                    onChange={() => setFollowMinChange("No")}
                    className="w-4 h-4"
                  />
                  <span>否</span>
                </label>
              </div>
            </div>

            <div className="card card-hover">
              <label className="block text-sm font-medium mb-2 text-white">条款遗漏审查</label>
              <div className="text-xs text-gray-400 mb-2">需检查的条款项目：</div>
              <textarea
                value={omissionItems}
                onChange={(e) => setOmissionItems(e.target.value)}
                placeholder="例如：提前无责解除权、知识产权归属、不可抗力条款、争议解决条款等。可按行或分号分隔多项。"
                className="w-full min-h-[100px] px-4 py-3 bg-gray-700/80 border border-gray-600 rounded-lg text-white placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
              />
            </div>

            {/* 特殊指令 */}
            <div className="card card-hover">
              <label className="block text-sm font-medium mb-3 text-white">特殊指令</label>
              <textarea
                value={specialInstructions}
                onChange={(e) => setSpecialInstructions(e.target.value)}
                placeholder="请输入特殊指令..."
                className="w-full min-h-[120px] px-4 py-3 bg-gray-700/80 border border-gray-600 rounded-lg text-white placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
              />
            </div>
            
            {/* 文件上传 */}
            <div className="card card-hover">
              <label className="block text-sm font-medium mb-3 text-white">上传合同文件</label>
              <div className="flex items-start gap-6">
                <label className="flex-1 cursor-pointer">
                  <div className="border-2 border-dashed border-gray-600 rounded-xl p-12 text-center hover:border-gray-500 hover:bg-gray-800/50 transition-all duration-200 shadow-inner min-h-[200px] flex flex-col items-center justify-center">
                    <div className="text-6xl mb-4">☁️</div>
                    <div className="text-gray-300 mb-2 text-sm">(点击)</div>
                    <div className="text-xs text-gray-400">(文件支持说明：支持 .txt、.md、.docx、.doc)</div>
                    {file && (
                      <div className="mt-4 text-sm text-blue-400 font-medium break-all px-4 max-w-full">{file.name}</div>
                    )}
                  </div>
                  <input
                    type="file"
                    accept=".doc,.docx,.txt,.md"
                    onChange={(e) => setFile(e.target.files?.[0] || null)}
                    className="hidden"
                  />
                </label>

                <button
                  onClick={onSubmit}
                  disabled={loading || !file}
                  className="px-10 py-12 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed rounded-xl font-medium text-lg transition-all duration-200 shadow-xl hover:shadow-2xl hover:scale-105 self-center"
                >
                  {loading ? "审查中..." : "审查"}
                </button>
              </div>
            </div>
          </div>

        </div>
      </main>
    );
  }
  
  // 结果页
  return (
    <main className="min-h-screen bg-gray-900 text-gray-100">
      <div className="flex h-screen">
        {/* 左侧面板 */}
        <div className="w-80 bg-gray-800 border-r-2 border-gray-700 p-6 overflow-y-auto shadow-2xl">
          <h1 className="text-2xl font-bold mb-6 text-white">AI合同审查系统</h1>
          
          {/* 显示选项 */}
          <div className="mb-6 card card-hover">
            <div className="text-sm font-medium mb-3 text-white">显示</div>
            <div className="space-y-2">
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={showRisks}
                  onChange={(e) => setShowRisks(e.target.checked)}
                  className="w-4 h-4"
                />
                <span>风险</span>
              </label>
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={showRevisions}
                  onChange={(e) => setShowRevisions(e.target.checked)}
                  className="w-4 h-4"
                />
                <span>修订</span>
              </label>
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={showOmissionCheck}
                  onChange={(e) => setShowOmissionCheck(e.target.checked)}
                  className="w-4 h-4"
                />
                <span>条款遗漏审查</span>
              </label>
            </div>
          </div>

          {/* 审查摘要 */}
          <div className="mb-8">
            <h2 className="text-xl font-semibold mb-4 text-white">审查摘要</h2>
            <div className="card card-hover min-h-[120px] text-gray-300">
              {result?.summary || "（无摘要）"}
            </div>
          </div>
          
          {/* 参数 */}
          <div className="mb-6 card card-hover">
            <div className="text-sm font-medium mb-2 text-white">参数</div>
            <div className="space-y-1 text-xs text-gray-400">
              <div>审查角度：{roleLabel}</div>
              <div>博弈地位：{bargainingPower === "Strong" ? "强势" : "弱势"}</div>
              <div>审查强度：{reviewDepth === "Detailed" ? "详细" : "粗略"}</div>
              <div>最小修订原则：{followMinChange === "Yes" ? "是" : "否"}</div>
              <div>输出偏好：包含风险与修订</div>
              <div>条款遗漏审查：{omissionItems.trim() ? omissionItems : "无"}</div>
              <div>特殊指令：{specialInstructions.trim() ? specialInstructions : "无"}</div>
            </div>
          </div>
          
          
          
          {/* 返回按钮 */}
          <button
            onClick={() => {
              setPage("home");
              setResult(null);
              setSelectedClause(null);
            }}
            className="mt-6 w-full px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded-xl text-sm transition-all duration-200 shadow-lg hover:shadow-xl"
          >
            返回上传页
          </button>
        </div>
        
        {/* 中间面板：全文预览 */}
        <div className="flex-[2] border-r-2 border-gray-700 p-8 overflow-y-auto">
          <h2 className="text-xl font-semibold mb-4 text-white">原文预览</h2>
          <div className="card card-hover max-h-[calc(100vh-160px)] overflow-y-auto text-gray-300 text-sm leading-relaxed">
            {previewParagraphs.length > 0 ? (
              <div className="space-y-3">
                {previewParagraphs.map((par, i) => (
                  <div key={i} ref={previewRefs[i]}>
                    {renderWithHighlight(par, i)}
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-gray-500 text-sm px-3 py-4">暂无原文预览</div>
            )}
          </div>
          <div className="mt-2 flex gap-2">
            <button onClick={clearHighlight} className="text-xs px-2 py-1 bg-gray-700 hover:bg-gray-600 rounded">清除定位</button>
          </div>
        </div>
        
          {/* 右侧面板：子条款风险/修订列表 */}
          <div className="w-2/5 bg-gray-800/60 p-6 rounded-r-2xl shadow-lg overflow-y-auto">
            <div className="max-h-full overflow-y-auto pr-2">
              {subClauseItems.length > 0 ? (
                <div className="space-y-4">
                  {subClauseItems
                    .filter(({ risks, revisions }) => {
                      const hasRisk = risks.length > 0;
                      const hasRevision = revisions.length > 0;
                      if (showRisks && showRevisions) {
                        return hasRisk && hasRevision;
                      }
                      if (showRisks) return hasRisk;
                      if (showRevisions) return hasRevision;
                      return false;
                    })
                    .map(({ subClauseId, risks, revisions, excerpts }) => {
                      const revisionForLocate =
                        revisions.find((rv) =>
                          (rv.excerpt_ids || []).some((id) => excerpts.some((ex) => ex.id === id))
                        ) || revisions[0];
                      const revisionLinkedExcerpt =
                        revisionForLocate &&
                        excerpts.find((ex) => revisionForLocate.excerpt_ids?.includes(ex.id || ""));
                      const originalFromRevision = revisionLinkedExcerpt?.text || "";
                      const clauseIdForLocate = revisionForLocate?.location || subClauseId;
                      const anchorText =
                        originalFromRevision ||
                        revisionForLocate?.anchor_text ||
                        risks[0]?.anchor_text ||
                        excerpts[0]?.text ||
                        subClauseId;
                      const extraSource =
                        originalFromRevision ||
                        revisionForLocate?.text ||
                        risks[0]?.risk ||
                        "";
                      return (
                        <div key={subClauseId} className="card bg-gray-800/80 p-0">
                          <div
                            className="w-full text-left px-4 py-3 bg-gray-700/50 hover:bg-gray-700/80 rounded-t-lg flex justify-between items-center transition-colors duration-200 cursor-pointer"
                            onClick={() =>
                              setCollapsedSubClauses((prev) => ({
                                ...prev,
                                [subClauseId]: !prev[subClauseId],
                              }))
                            }
                          >
                            <div className="flex items-center">
                              <button
                                onClick={(e) => {
                                  e.stopPropagation();
                                  locateByAnchor(anchorText, extraSource, clauseIdForLocate);
                                }}
                                className="text-blue-400 hover:underline text-sm"
                              >
                                定位
                              </button>
                              <span
                                className={`transform transition-transform duration-200 ml-4 ${
                                  collapsedSubClauses[subClauseId] ? "rotate-180" : "rotate-0"
                                }`}
                              >
                                <svg
                                  xmlns="http://www.w3.org/2000/svg"
                                  className="h-5 w-5"
                                  viewBox="0 0 20 20"
                                  fill="currentColor"
                                >
                                  <path
                                    fillRule="evenodd"
                                    d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z"
                                    clipRule="evenodd"
                                  />
                                </svg>
                              </span>
                            </div>
                          </div>
                          {!collapsedSubClauses[subClauseId] && (
                            <div className="p-4 space-y-4">
                              {showRisks && risks.length > 0 && (
                                <div>
                                  <h4 className="font-semibold text-red-400 mb-2">风险提示</h4>
                                  <div className="space-y-3 text-sm text-gray-300">
                                    {risks.map((risk, i) => {
                                      const rawTitle = risk.title || "";
                                      const levelMatch = rawTitle.match(/^\s*\[(High|Medium|Low)\]\s*(.*)$/i);
                                      const levelRaw = levelMatch ? levelMatch[1] : "";
                                      const titleText = levelMatch ? levelMatch[2] || rawTitle : rawTitle;
                                      let levelLabel = "";
                                      if (levelRaw.toLowerCase() === "high") levelLabel = "高";
                                      else if (levelRaw.toLowerCase() === "medium") levelLabel = "中";
                                      else if (levelRaw.toLowerCase() === "low") levelLabel = "低";
                                      return (
                                        <div key={`risk-${i}`} className="border border-red-500/20 rounded-lg px-3 py-2 bg-red-900/5">
                                          <div className="font-semibold text-red-300 mb-1">
                                            {titleText}
                                            {levelLabel && (
                                              <span className="ml-2 text-xs font-normal text-red-200">
                                                风险等级：{levelLabel}
                                              </span>
                                            )}
                                          </div>
                                          <div>{risk.risk}</div>
                                          {risk.impact && (
                                            <div className="mt-1 text-red-200 text-xs">可能后果：{risk.impact}</div>
                                          )}
                                          {risk.suggestion && (
                                            <div className="mt-1 text-xs text-gray-400">
                                              修改建议：{risk.suggestion}
                                            </div>
                                          )}
                                        </div>
                                      );
                                    })}
                                  </div>
                                </div>
                              )}
                              {showRevisions && revisions.length > 0 && (
                                <div>
                                  <h4 className="font-semibold text-blue-400 mb-2">修订建议</h4>
                                  <div className="space-y-3 text-sm text-gray-300">
                                    {revisions.map((rev, i) => {
                                      const originalExcerpt = excerpts.find((ex) =>
                                        rev.excerpt_ids?.includes(ex.id || "")
                                      );
                                      const originalText = originalExcerpt?.text || "";
                                      return (
                                        <div key={`rev-${i}`} className="border border-blue-500/20 rounded-lg px-3 py-2 bg-blue-900/5">
                                          {rev.type === "新增" && (
                                            <div className="mb-1 text-xs text-blue-300">新增条款</div>
                                          )}
                                          <div>{smartCompareRevision(rev.text || "", originalText)}</div>
                                        </div>
                                      );
                                    })}
                                  </div>
                                </div>
                              )}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  {showOmissionCheck && (
                    <div className="card bg-gray-800/80">
                      <h3 className="text-base font-semibold text-yellow-300 mb-2">条款遗漏审查</h3>
                      {omissionItems.trim() ? (
                        result?.omission_review?.trim() ? (
                          <pre className="whitespace-pre-wrap text-xs text-gray-200 leading-relaxed">
                            {result.omission_review}
                          </pre>
                        ) : (
                          <div className="text-xs text-gray-400">暂无条款遗漏审查结果</div>
                        )
                      ) : (
                        <div className="text-xs text-gray-400">无条款遗漏审查需求</div>
                      )}
                    </div>
                  )}
                </div>
              ) : (
                <div className="text-center text-gray-400 py-16">
                  <p>审查结果将在此处显示</p>
                </div>
              )}
            </div>
          </div>
      </div>
    </main>
  );
}
