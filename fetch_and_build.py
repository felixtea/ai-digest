import feedparser
import json
import os
import requests
from datetime import datetime
from jinja2 import Environment, FileSystemLoader
from google import genai
from pydantic import BaseModel
from typing import List

# ==========================================
# 1. 配置部分 (Configuration)
# ==========================================
# 这里我们选用几个最有代表性的源用于演示
RSS_FEEDS = {
    "OpenAI": "https://openai.com/news/rss.xml",
    "Anthropic": "https://www.anthropic.com/feed.xml",
    "Hugging Face": "https://huggingface.co/blog/feed.xml",
    "Simon Willison": "https://simonwillison.net/atom/everything/"
}

# Jinja2 模板配置
TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")
PUBLIC_DIR = os.path.join(os.path.dirname(__file__), "public")
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

os.makedirs(PUBLIC_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

# Gemini API (如果环境变量没有设置，我们将使用 Mock 数据)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# ==========================================
# 2. 抓取 RSS 数据 (Fetch Data)
# ==========================================
def fetch_rss_feeds():
    print("🌍 正在抓取 RSS 源...")
    articles = []
    
    for source_name, url in RSS_FEEDS.items():
        try:
            print(f"  -> 抓取: {source_name}")
            feed = feedparser.parse(url)
            # 为了防止内容过多，每个源只取最近 5 篇文章
            for entry in feed.entries[:5]: 
                articles.append({
                    "title": entry.title,
                    "link": entry.link,
                    "source": source_name,
                    # 某些 RSS 源的 summary 包含 HTML 标签，这里做个简单截取
                    "summary": entry.get("summary", "")[:200]
                })
        except Exception as e:
            print(f"  [错误] 抓取 {source_name} 失败: {e}")
            
    return articles

# 定义预期的 JSON 输出结构 (Pydantic Model)
class NewsItem(BaseModel):
    title: str
    summary: str
    source: str

class CuratedNews(BaseModel):
    articles: List[NewsItem]

# ==========================================
# 3. 使用 AI 模型筛选并生成摘要 (AI Curation)
# ==========================================
def curate_with_ai(articles):
    if not GEMINI_API_KEY:
        print("⚠️ 未检测到 GEMINI_API_KEY，正在使用模拟 (Mock) 数据用于测试 HTML 渲染...")
        return [
            {
                "title": "OpenAI Releases Powerful New Developer Framework",
                "summary": "OpenAI has officially launched a new open-source agent framework, setting a new standard for AI application generation and autonomous execution.",
                "source": "OpenAI"
            },
            {
                "title": "Anthropic Claude 4 Exceeds Expectations in Coding Benchmarks",
                "summary": "The latest model from Anthropic has pushed the boundaries of context windows, performing flawlessly on SWE-bench and coding tasks.",
                "source": "Anthropic"
            },
            {
                "title": "Hugging Face Crosses 2 Million Open Source Models",
                "summary": "The open-source AI community continues its massive scaling trend, with Hugging Face becoming the definitive hub for open weights.",
                "source": "Hugging Face"
            }
        ], len(articles)

    print("🤖 正在调用 Gemini API 处理抓取到的", len(articles), "篇文章...")
    client = genai.Client(api_key=GEMINI_API_KEY)
    
    # 构造给 AI 的提示词
    prompt = f"""
    你是一个资深的 AI 领域科技编辑。以下是今天抓取到的最新 AI 资讯（共 {len(articles)} 条）。
    请你评估它们的重要性，从中挑选出【最核心、最具影响力的 3 条新闻】。
    对于这 3 条新闻，用专业的英文重新拟定一个简短醒目的 headline（标题），并提供 1-2 句简洁精炼的 summary（摘要）。

    今日新闻列表：
    {json.dumps(articles, ensure_ascii=False)}
    """
    
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config={
                'response_mime_type': 'application/json',
                'response_schema': CuratedNews,
                'system_instruction': "You are a pragmatic, hype-free tech editor. Return the top 3 most important news items."
            },
        )
        
        # 将 Pydantic 对象转换为字典列表以便模板渲染
        curated_data = response.parsed.model_dump()["articles"]
        return curated_data, len(articles)
        
    except Exception as e:
        print(f"❌ 调用 AI 失败: {e}")
        return [], 0

# ==========================================
# 4. 生成静态网站 (Generate Static HTML)
# ==========================================
def build_website(curated_news, total_sources):
    print("🏗️ 正在生成静态 HTML...")
    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR))
    template = env.get_template("index.html")
    
    today_str = datetime.now().strftime("%B %d, %Y")
    
    html_content = template.render(
        date=today_str,
        news=curated_news,
        total_sources=total_sources
    )
    
    # 将生成的 HTML 写入到 public 文件夹
    output_path = os.path.join(PUBLIC_DIR, "index.html")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)
        
    print(f"✅ 网站生成成功！请在浏览器打开: {output_path}")

# ==========================================
# 主运行逻辑 (Main)
# ==========================================
if __name__ == "__main__":
    print(f"🚀 开始构建 AI Digest Clone...")
    # 1. 抓取
    raw_articles = fetch_rss_feeds()
    
    # 2. 存一份 raw data 备查 (可选)
    with open(os.path.join(DATA_DIR, "raw_articles.json"), "w", encoding="utf-8") as f:
        json.dump(raw_articles, f, ensure_ascii=False, indent=2)
        
    # 3. AI 筛选处理
    curated_news, total_count = curate_with_ai(raw_articles)
    
    # 4. 渲染输出
    if curated_news:
        build_website(curated_news, total_count)
    else:
        print("❌ 未获取到可用的筛选数据，停止生成。")
