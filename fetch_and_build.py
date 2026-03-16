import feedparser
import json
import os
import glob
import requests
import difflib
from datetime import datetime
from jinja2 import Environment, FileSystemLoader
from google import genai
from pydantic import BaseModel
from typing import List

# ==========================================
# 1. 25 个顶级高质量 AI 资讯 RSS 源
# ==========================================
RSS_FEEDS = {
    # 1. Primary Sources (官方消息)
    "OpenAI News": "https://openai.com/news/rss.xml",
    "Anthropic": "https://www.anthropic.com/feed.xml",
    "Google DeepMind": "https://deepmind.google/blog/rss.xml",
    "Meta AI": "https://ai.meta.com/blog/rss/",
    "Hugging Face": "https://huggingface.co/blog/feed.xml",
    "NVIDIA Tech Blog": "https://developer.nvidia.com/blog/category/artificial-intelligence/feed/",
    "Microsoft Research": "https://www.microsoft.com/en-us/research/feed/",
    
    # 2. Industry Newsletters & Experts (专家与分析)
    "Import AI (Jack Clark)": "https://importai.substack.com/feed",
    "Latent Space": "https://www.latent.space/feed",
    "One Useful Thing": "https://www.oneusefulthing.org/feed",
    "Simon Willison": "https://simonwillison.net/atom/everything/",
    "AI Snake Oil": "https://www.aisnakeoil.com/feed",
    "Interconnects": "https://www.interconnects.ai/feed",
    "Ahead of AI": "https://magazine.sebastianraschka.com/feed",
    "Ben's Bites": "https://bensbites.beehiiv.com/rss",
    
    # 3. High-Quality Tech Media (科技媒体)
    "MIT Tech Review (AI)": "https://www.technologyreview.com/topic/artificial-intelligence/feed/",
    "TechCrunch (AI)": "https://techcrunch.com/category/artificial-intelligence/feed/",
    "The Verge (AI)": "https://www.theverge.com/rss/artificial-intelligence/index.xml",
    "Ars Technica (AI)": "https://arstechnica.com/tag/ai/feed/",
    "404 Media": "https://www.404media.co/rss/",
    "InfoQ (AI/ML)": "https://feed.infoq.com/ai-ml-data-eng/news",
    
    # 4. Academic & Community (社区与前沿)
    "Hacker News": "https://news.ycombinator.com/rss",
    "arXiv CS.AI": "https://export.arxiv.org/rss/cs.AI",
    "arXiv CS.CL": "https://export.arxiv.org/rss/cs.CL"
}

# 目录配置：增加中英文输出目录
TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")
PUBLIC_DIR = os.path.join(os.path.dirname(__file__), "public")
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

EN_DIR = os.path.join(PUBLIC_DIR, "en")
ZH_DIR = os.path.join(PUBLIC_DIR, "zh")
EN_DIGEST_DIR = os.path.join(EN_DIR, "digest")
ZH_DIGEST_DIR = os.path.join(ZH_DIR, "digest")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(EN_DIGEST_DIR, exist_ok=True)
os.makedirs(ZH_DIGEST_DIR, exist_ok=True)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# ==========================================
# 2. 抓取 RSS 数据 (取每源 top 5, 保存 pubdate)
# ==========================================
# Source Authority Tiers (影响评分权重)
SOURCE_AUTHORITY = {
    # Tier 1: Official primary sources (最高权重)
    "OpenAI News": 1.0, "Anthropic": 1.0, "Google DeepMind": 1.0,
    "Meta AI": 1.0, "Hugging Face": 0.9, "NVIDIA Tech Blog": 0.9,
    "Microsoft Research": 0.9,
    # Tier 2: Expert newsletters (高权重)
    "Import AI (Jack Clark)": 0.85, "Latent Space": 0.85,
    "One Useful Thing": 0.8, "Simon Willison": 0.85,
    "AI Snake Oil": 0.8, "Interconnects": 0.8, "Ahead of AI": 0.8,
    # Tier 3: Quality tech media (中等权重)
    "MIT Tech Review (AI)": 0.75, "TechCrunch (AI)": 0.7,
    "The Verge (AI)": 0.7, "Ars Technica (AI)": 0.72,
    "404 Media": 0.68, "InfoQ (AI/ML)": 0.65,
    # Tier 4: Community (社区热度)
    "Hacker News": 0.6, "Ben's Bites": 0.6,
    "arXiv CS.AI": 0.7, "arXiv CS.CL": 0.7,
}

def fetch_rss_feeds():
    print("🌍 正在从 25 个精选信源抓取今日 AI 动态...")
    articles = []
    
    for source_name, url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:5]:  # Phase 6: 取 top 5 以增加聚类原料
                published = entry.get("published", entry.get("updated", ""))
                articles.append({
                    "title": entry.title,
                    "link": entry.link,
                    "source": source_name,
                    "summary": entry.get("summary", "")[:500],  # 更多摘要上下文
                    "published": published
                })
            print(f"✅ 抓取完成: {source_name} ({len(feed.entries[:5])}篇)")
        except Exception as e:
            print(f"⚠️ 抓取失败: {source_name} - {str(e)}")
            
    return articles

# ==========================================
# 3. Phase 6: 编辑流水线 (新闻聚类、评分)
# ==========================================
def cluster_articles(articles):
    """将相似标题的文章聚合为同一个事件集群, 防止重复新闻"""
    clusters = []
    
    for article in articles:
        title = article["title"].lower()
        matched = False
        for cluster in clusters:
            # 用序列相似度比较标题
            ratio = difflib.SequenceMatcher(None, title, cluster["canonical_title"].lower()).ratio()
            if ratio > 0.45:  # 阈值: 45%相似度即视为同一事件
                cluster["articles"].append(article)
                matched = True
                break
        if not matched:
            clusters.append({
                "canonical_title": article["title"],
                "articles": [article]
            })
    
    print(f"📦 聚类完成: {len(articles)} 条原始文章 → {len(clusters)} 个事件集群")
    return clusters


def score_clusters(clusters):
    """对每个事件集群打分: 多源交叉验证 + 权威度 + 最新性"""
    scored = []
    
    for cluster in clusters:
        arts = cluster["articles"]
        
        # 1. 跨信源分: 单源 0.0, 多源加分
        unique_sources = len(set(a["source"] for a in arts))
        cross_source_score = min(1.0, (unique_sources - 1) * 0.4)
        
        # 2. 权威度分: 集群内最高权威值
        authority_score = max(SOURCE_AUTHORITY.get(a["source"], 0.5) for a in arts)
        
        # 3. 新鲜度分: 有 pubdate 的文章加分 (简单启发)
        recency_score = 0.6 if any(a["published"] for a in arts) else 0.3
        
        # 加权总分
        total = (0.40 * cross_source_score +
                 0.40 * authority_score +
                 0.20 * recency_score)
        
        scored.append({
            **cluster,
            "score": round(total, 4),
            "unique_sources": unique_sources,
        })
    
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored

# ==========================================
# 3. 极速深度的结构化双语 JSON 模型 (The "i18n & Deep Integration" Model)
# ==========================================
class BilingualString(BaseModel):
    en: str
    zh: str

class BilingualList(BaseModel):
    en: List[str]
    zh: List[str]

class NewsItem(BaseModel):
    title: BilingualString               
    tl_dr: BilingualString               
    key_takeaways: BilingualList 
    impact: BilingualString              
    source: str
    link: str # 新增原始链接

class HonorableMention(BaseModel):
    title: BilingualString
    source: str
    link: str

class CuratedNews(BaseModel):
    top_articles: List[NewsItem]
    honorable_mentions: List[HonorableMention]

# ==========================================
# 4. 使用 Gemini 进行高阶双语合成
# ==========================================
# Selection schema for Tier 1 (pure index selectors)
class SelectedIndex(BaseModel):
    cluster_indices: List[int]   # indices of selected clusters from the ranked list
    mention_indices: List[int]   # indices to use as honorable mentions

def curate_with_ai(scored_clusters, all_articles):
    """Phase 6: Two-tier LLM. Tier-1=Editor selects. Tier-2=Writer composes."""
    if not GEMINI_API_KEY:
        print("⚠️ 未检测到 GEMINI_API_KEY。使用高级测试数据（Mock）...")
        return {
            "top_articles": [
                {
                    "title": {"en": "Anthropic Unveils Next-Generation Claude Models", "zh": "Anthropic发布下一代Claude模型，主导代码基准测试"},
                    "tl_dr": {"en": "Anthropic released its newest Claude, outperforming GPT-4.", "zh": "Anthropic发布最新Claude，性能超越GPT-4。"},
                    "key_takeaways": {
                        "en": ["Record 90%+ on SWE-bench.", "2M token context window.", "New Agentic Actions API."],
                        "zh": ["SWE-bench上达到创纪录的90%+。", "上下文窗口扩展至200万Token。", "全新Agentic Actions API。"]
                    },
                    "impact": {"en": "Directly challenges OpenAI's enterprise dominance.", "zh": "直接挑战OpenAI在企业领域的统治地位。"},
                    "source": "Anthropic",
                    "link": "https://anthropic.com"
                }
            ],
            "honorable_mentions": [
                {
                    "title": {"en": "Google Robot Framework Open Sourced", "zh": "Google 开源机器人基础框架"},
                    "source": "Google DeepMind", "link": "https://deepmind.google"
                }
            ]
        }, len(all_articles)

    client = genai.Client(api_key=GEMINI_API_KEY)
    top_n = scored_clusters[:15]  # 仅将高分的前15簇送入编辑层

    # ---- Tier 1: The Editor — Select what to write ----
    print("📋 [Tier 1 / Editor] 正在请 Gemini 从预排序集群中选题...")
    editor_prompt = f"""You are a senior editor for a bilingual AI daily briefing.
Below are {len(top_n)} PRE-RANKED news event clusters (already sorted by importance score).
Each cluster may contain multiple articles covering the same event.

Your task:
1. Select the indices (0-based) of exactly 3 clusters for deep analysis. Prioritize:
   - Clusters covering genuinely NEW developments (not commentary/opinion)
   - Clusters with high cross-source coverage (multiple sources = verified)
   - Diversity of topics (don't pick 3 OpenAI stories if alternative big stories exist)
2. Select 5 additional indices as Honorable Mentions (brief, no overlap with top 3).

Return ONLY valid JSON with fields: cluster_indices (list of 3 ints), mention_indices (list of 5 ints).

Clusters:
{json.dumps([{"index": i, "title": c["canonical_title"], "sources": list(set(a["source"] for a in c["articles"])), "score": c["score"]} for i, c in enumerate(top_n)], ensure_ascii=False)}
"""
    try:
        editor_resp = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=editor_prompt,
            config={
                'response_mime_type': 'application/json',
                'response_schema': SelectedIndex,
                'system_instruction': "You are a strict editorial selector. Return only valid JSON."
            }
        )
        selected = editor_resp.parsed
        story_indices = [i for i in selected.cluster_indices if i < len(top_n)][:3]
        mention_idx = [i for i in selected.mention_indices if i < len(top_n)][:5]
    except Exception as e:
        print(f"⚠️ Tier-1 选题失败，回退到 Top 3: {e}")
        story_indices = [0, 1, 2]
        mention_idx = [3, 4, 5, 6, 7]

    print(f"✅ 编辑选题完成: 精选 {story_indices}, 快讯 {mention_idx}")

    # ---- Tier 2: The Writer — Write each selected story ----
    print("✍️ [Tier 2 / Writer] 正在逐篇深度撰写双语内容...")
    top_articles = []

    for idx in story_indices:
        cluster = top_n[idx]
        # Build mini-context from all articles in this cluster
        context_parts = []
        for a in cluster["articles"]:
            context_parts.append(f"Source: {a['source']}\nTitle: {a['title']}\nURL: {a['link']}\nExcerpt: {a['summary']}")
        context = "\n\n---\n".join(context_parts)

        writer_prompt = f"""You are a bilingual tech journalist writing one story for a premium daily digest.
Write ONLY using facts present in the source material below. Do NOT add external knowledge or speculation.
The output must be in BOTH English and Simplified Chinese (native quality, not machine-translated).

Instructions:
- title: Punchy, WSJ-style headline. No clickbait. (en + zh)
- tl_dr: 1 sentence, exactly what factually happened. (en + zh)
- key_takeaways: Exactly 3 bullet points with specific technical/numerical details. (en + zh)
- impact: 1-2 sentences. WHY this matters for the AI industry. Sober, not hype. (en + zh)
- source: Name of the primary/most authoritative source
- link: The most authoritative URL from the cluster

Source Material:
{context}
"""
        try:
            writer_resp = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=writer_prompt,
                config={
                    'response_mime_type': 'application/json',
                    'response_schema': NewsItem,
                    'system_instruction': "You are a neutral, precise bilingual tech writer. Facts only, no hype."
                }
            )
            top_articles.append(writer_resp.parsed.model_dump())
            print(f"  ✅ 完成第 {len(top_articles)} 篇")
        except Exception as e:
            print(f"  ⚠️ 第 {idx} 篇写作失败: {e}")

    # ---- Honorable Mentions (lightweight — just bilingual title) ----
    honorable_mentions = []
    for idx in mention_idx:
        cluster = top_n[idx]
        best = cluster["articles"][0]
        honorable_mentions.append({
            "title": {"en": cluster["canonical_title"], "zh": cluster["canonical_title"]},
            "source": best["source"],
            "link": best["link"]
        })

    return {"top_articles": top_articles, "honorable_mentions": honorable_mentions}, len(all_articles)


# ==========================================
# 5. 生成极其透明的双语静态网站
# ==========================================
def render_translation(template, data_dict, lang, base_path, today_date_id, total_sources, raw_articles, historical=False):
    """提取的通用渲染逻辑，支持 en 和 zh 两种语言切片"""
    
    # 构建当前语言的字典树
    localized_top = []
    for item in data_dict.get("top_articles", []):
        localized_top.append({
            "title": item["title"][lang],
            "tl_dr": item["tl_dr"][lang],
            "key_takeaways": item["key_takeaways"][lang],
            "impact": item["impact"][lang],
            "source": item["source"],
            "link": item["link"]
        })
        
    localized_mentions = []
    for item in data_dict.get("honorable_mentions", []):
        localized_mentions.append({
            "title": item["title"][lang],
            "source": item["source"],
            "link": item["link"]
        })

    date_obj = datetime.strptime(today_date_id, "%Y-%m-%d")
    if lang == "zh":
        display_date = date_obj.strftime("%Y年%m月%d日")
        ui = {
            "title": "AI资讯速览",
            "tagline": "25 个顶尖英文一手信源，如实呈现。",
            "nav_about": "方法论",
            "nav_archive": "存档",
            "nav_switch_lang": "English",
            "nav_switch_url": f"../en/digest/{today_date_id}.html" if historical else "../en/index.html",
            "stat_text": f"今日从 {total_sources} 条源数据中由 AI 筛选生成。",
            "mentions_title": "候选资讯",
            "tl_dr_label": "TL;DR",
            "impact_label": "为何重要",
            "footer_text": "由 Python, Jinja2 及 Google Gemini 2.5 Flash 驱动生成。"
        }
    else:
        display_date = date_obj.strftime("%B %d, %Y")
        ui = {
            "title": "AI News Digest",
            "tagline": "25 sources. 3 stories. Actionable Intelligence.",
            "nav_about": "Methodology",
            "nav_archive": "Archive",
            "nav_switch_lang": "中文",
            "nav_switch_url": f"../zh/digest/{today_date_id}.html" if historical else "../zh/index.html",
            "stat_text": f"Automated curation from {total_sources} recent items today.",
            "mentions_title": "Honorable Mentions",
            "tl_dr_label": "TL;DR",
            "impact_label": "Why it matters",
            "footer_text": "Built with Python, Jinja2, and Google Gemini 2.5 Flash."
        }

    return template.render(
        lang=lang,
        date=display_date,
        top_articles=localized_top,
        mentions=localized_mentions,
        base_path=base_path,
        ui=ui,
        date_id=today_date_id,
        raw_links=raw_articles
    ), ui

def build_websites(today_data, today_date_str, raw_articles):
    print("🏗️ 正在构建双语静态体系 (EN / ZH) 并恢复全量归档地图...")
    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR))
    index_template = env.get_template("index.html")
    archive_template = env.get_template("archive.html")
    sources_template = env.get_template("sources.html")
    method_template = env.get_template("methodology.html")
    
    # 1. 保存今天的源数据
    today_json_path = os.path.join(DATA_DIR, f"{today_date_str}.json")
    save_data = {
        "date_id": today_date_str,
        "total_sources": today_data["total_sources"],
        "curated": today_data["curated"],
        "raw_links": [a["link"] for a in raw_articles]
    }
    with open(today_json_path, "w", encoding="utf-8") as f:
        json.dump(save_data, f, ensure_ascii=False, indent=2)

    # 2. 读取所有的历史 JSON 数据以构建归档列表
    all_digests = []
    for filepath in glob.glob(os.path.join(DATA_DIR, "*.json")):
        filename = os.path.basename(filepath)
        if filename == "raw_articles.json": continue
            
        date_id = filename.replace(".json", "")
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
            # 对于历史列表，我们仅需要极轻量的数据
            all_digests.append({
                "date_id": date_id,
                "total_sources": data.get("total_sources", 0)
            })
    all_digests.sort(key=lambda x: x["date_id"], reverse=True)

    def format_date_for_archive(date_str, lang):
        date_obj = datetime.strptime(date_str, "%Y-%m-%d")
        return date_obj.strftime("%Y年%m月%d日") if lang == "zh" else date_obj.strftime("%B %d, %Y")

    def build_for_language(lang, out_dir, hist_dir):
        # 构建归档列表的显示日期
        lang_digests = []
        for d in all_digests:
            lang_digests.append({
                "date_id": d["date_id"],
                "display_date": format_date_for_archive(d["date_id"], lang),
                "total_sources": d["total_sources"]
            })

        # A: 首页 (index.html)
        html_idx, ui = render_translation(index_template, today_data["curated"], lang, "../", today_date_str, today_data["total_sources"], raw_articles, False)
        with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as f: f.write(html_idx)

        # B: 历史详情页 (digest/YYYY-MM-DD.html)
        html_hist, _ = render_translation(index_template, today_data["curated"], lang, "../../", today_date_str, today_data["total_sources"], raw_articles, True)
        with open(os.path.join(hist_dir, f"{today_date_str}.html"), "w", encoding="utf-8") as f: f.write(html_hist)

        # C: 透明源记录页 (digest/YYYY-MM-DD-sources.html)
        ui["nav_switch_url"] = f"../../{'en' if lang=='zh' else 'zh'}/digest/{today_date_str}-sources.html"
        html_src, _ = render_translation(sources_template, today_data["curated"], lang, "../../", today_date_str, today_data["total_sources"], [a["link"] for a in raw_articles], True)
        with open(os.path.join(hist_dir, f"{today_date_str}-sources.html"), "w", encoding="utf-8") as f: f.write(html_src)
        
        # D: 归档列表页 (archive.html)
        ui["nav_switch_url"] = f"../{'en' if lang=='zh' else 'zh'}/archive.html"
        html_arc = archive_template.render(lang=lang, ui=ui, base_path="../", digests=lang_digests)
        with open(os.path.join(out_dir, "archive.html"), "w", encoding="utf-8") as f: f.write(html_arc)

        # E: 方法论页 (methodology.html)
        ui["nav_switch_url"] = f"../{'en' if lang=='zh' else 'zh'}/methodology.html"
        html_mth = method_template.render(lang=lang, ui=ui, base_path="../")
        with open(os.path.join(out_dir, "methodology.html"), "w", encoding="utf-8") as f: f.write(html_mth)

    # 3. 双语通道分别执行全家桶渲染
    build_for_language("zh", ZH_DIR, ZH_DIGEST_DIR)
    build_for_language("en", EN_DIR, EN_DIGEST_DIR)

    print(f"✅ 双语网站及所有周边页面构建成功！共 {len(all_digests)} 天数据。")

# ==========================================
# 本地路由重定向 (让 /index.html 自动跳到 /zh/ 或 /en/)
# ==========================================
def generate_root_redirect():
    content = """<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="robots" content="noindex">
<script>
(function(){
  var lang = (navigator.language || navigator.userLanguage || "").slice(0,2);
  if(lang === "zh") {
      window.location.replace("zh/index.html");
  } else {
      window.location.replace("en/index.html");
  }
})();
</script>
<noscript><meta http-equiv="refresh" content="0;url=zh/index.html"></noscript>
</head>
<body></body>
</html>"""
    with open(os.path.join(PUBLIC_DIR, "index.html"), "w") as f:
        f.write(content)

def generate_rss_feed():
    """生成 Atom RSS feed, 遍历所有历史 JSON 文件构建完整订阅源"""
    from xml.etree.ElementTree import Element, SubElement, tostring
    import xml.etree.ElementTree as ET

    feed_el = Element("feed", xmlns="http://www.w3.org/2005/Atom")
    SubElement(feed_el, "title").text = "AI News Digest | AI资讯速览"
    SubElement(feed_el, "link", href="https://ai-digest.liziran.com/en/", rel="alternate")
    SubElement(feed_el, "link", href="https://ai-digest.liziran.com/feed.xml", rel="self")
    SubElement(feed_el, "id").text = "https://ai-digest.liziran.com/"
    SubElement(feed_el, "updated").text = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")

    all_json = sorted(glob.glob(os.path.join(DATA_DIR, "????-??-??.json")), reverse=True)
    for path in all_json[:30]:  # 只输出最近 30 期
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            date_id = d.get("date_id", os.path.basename(path).replace(".json",""))
            curated = d.get("curated", {})
            tops = curated.get("top_articles", [])
            if not tops: continue
            first = tops[0]

            entry = SubElement(feed_el, "entry")
            SubElement(entry, "title").text = first.get("title", {}).get("en", date_id)
            SubElement(entry, "link", href=f"https://ai-digest.liziran.com/en/digest/{date_id}.html")
            SubElement(entry, "id").text = f"https://ai-digest.liziran.com/en/digest/{date_id}.html"
            SubElement(entry, "updated").text = f"{date_id}T00:00:00Z"
            summary = first.get("tl_dr", {}).get("en", "")
            SubElement(entry, "summary").text = summary

        except Exception as e:
            print(f"⚠️ RSS 跳过 {path}: {e}")

    xml_str = ET.tostring(feed_el, encoding="unicode", xml_declaration=False)
    rss_path = os.path.join(PUBLIC_DIR, "feed.xml")
    with open(rss_path, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="utf-8"?>\n' + xml_str)
    print(f"📡 RSS feed 已生成: {rss_path}")


if __name__ == "__main__":
    print("🚀 开始构建 AI Digest (Phase 6: 编辑流水线)...")
    
    # === Phase 6 Pipeline: fetch → cluster → score → curate → build ===
    raw_articles = fetch_rss_feeds()
    clusters = cluster_articles(raw_articles)
    scored = score_clusters(clusters)
    
    curated_news, total_count = curate_with_ai(scored, raw_articles)
    
    if curated_news:
        today_date_str = datetime.now().strftime("%Y-%m-%d")
        today_data = {
            "curated": curated_news,
            "total_sources": total_count
        }
        build_websites(today_data, today_date_str, raw_articles)
        generate_root_redirect()
        generate_rss_feed()
        print("🎉 完整流水线执行完毕。")
    else:
        print("❌ 流程意外终止。")

