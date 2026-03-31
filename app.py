"""
TRACTIAN GTM Dashboard
======================
A Streamlit app where you type in a company name + website,
and it runs the full pipeline and shows you the scored output.

HOW TO RUN:
  pip install streamlit requests beautifulsoup4 lxml duckduckgo-search playwright anthropic pandas openpyxl
  playwright install chromium
  streamlit run app.py
"""

import os, json, re, time, asyncio
import requests
import pandas as pd
import anthropic
import streamlit as st
from bs4 import BeautifulSoup
from duckduckgo_search import DDGS
from playwright.async_api import async_playwright

# ── PAGE CONFIG ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="TRACTIAN Account Scorer",
    page_icon="🏭",
    layout="wide"
)

st.title("🏭 TRACTIAN Account Scorer")
st.caption("Enter a company name and website to score their ICP fit and map their facilities.")

# ── SIDEBAR: API KEY ──────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Settings")
    api_key = st.text_input("Anthropic API Key", type="password", placeholder="sk-ant-...")
    st.caption("Get your key at console.anthropic.com")
    st.divider()
    st.markdown("**Scoring weights:**")
    st.markdown("- 🏭 Manufacturing: 30% (max 3.0)")
    st.markdown("- 🏗️ Physical Assets: 30% (max 3.0)")
    st.markdown("- ⚡ Downtime Sensitivity: 25% (max 2.5)")
    st.markdown("- 🌍 Global Operations: 15% (max 1.5)")


# ── INPUT FORM ────────────────────────────────────────────────────────────────
col1, col2 = st.columns(2)
with col1:
    company_name = st.text_input("Company Name", placeholder="e.g. Kraft Heinz")
with col2:
    company_website = st.text_input("Company Website", placeholder="e.g. https://www.kraftheinzcompany.com")

run_button = st.button("🔍 Analyze Company", type="primary", use_container_width=True)


# ── HELPERS ───────────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
    )
}

PAGES_TO_TRY = [
    "", "/about-us", "/about", "/company", "/locations",
    "/our-locations", "/manufacturing", "/facilities",
    "/operations", "/investor-relations", "/sustainability",
]

def html_to_text(html, max_chars=5000):
    if not html: return ""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    return " ".join(soup.get_text(separator=" ").split())[:max_chars]

def clean_base_url(url):
    url = url.rstrip("/")
    for suffix in ["/en-us.html", "/en-us", "/us/en-us", "/us/en", "/en"]:
        if url.endswith(suffix):
            url = url[:-len(suffix)]
    return url

def simple_fetch(url, timeout=10):
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        if r.status_code in [403, 429, 404]: return None
        r.raise_for_status()
        return r.text
    except: return None


# ── STAGE 1: DATA COLLECTION ──────────────────────────────────────────────────

async def scrape_website(name, website, status):
    status.update(label=f"🌐 Scraping {name}'s website...")
    base  = clean_base_url(website)
    found = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page    = await browser.new_page()
        await page.set_extra_http_headers({"User-Agent": HEADERS["User-Agent"]})

        for path in PAGES_TO_TRY:
            url = base + path
            try:
                await page.goto(url, timeout=10000, wait_until="domcontentloaded")
                await page.wait_for_timeout(600)
                html = await page.content()
                if html and len(html) > 500:
                    text = html_to_text(html)
                    if len(text) > 200:
                        found[path or "/"] = {"url": url, "text": text}
            except: pass
            time.sleep(0.3)

        await browser.close()

    return found


def fetch_wikipedia(name, status):
    status.update(label=f"📖 Checking Wikipedia for {name}...")
    url  = f"https://en.wikipedia.org/wiki/{name.replace(' ', '_')}"
    html = simple_fetch(url)

    if not html or "may refer to" in (html or "") or "disambiguation" in (html or ""):
        api  = f"https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch={requests.utils.quote(name + ' company')}&format=json&srlimit=1"
        data = simple_fetch(api)
        if data:
            try:
                hits = json.loads(data).get("query", {}).get("search", [])
                if hits:
                    title = hits[0]["title"].replace(" ", "_")
                    url   = f"https://en.wikipedia.org/wiki/{title}"
                    html  = simple_fetch(url)
            except: pass

    if html:
        return {"url": url, "text": html_to_text(html, max_chars=5000)}
    return {}


def fetch_sec(name, status):
    status.update(label=f"📋 Checking SEC EDGAR for {name}...")
    url  = f"https://efts.sec.gov/LATEST/search-index?q=%22{requests.utils.quote(name)}%22&forms=10-K&dateRange=custom&startdt=2023-01-01"
    html = simple_fetch(url)
    return {"search_results": html[:3000]} if html else {}


def run_searches(name, website, status):
    status.update(label=f"🔎 Searching the web for {name}...")
    domain  = clean_base_url(website).replace("https://www.", "").replace("https://", "")
    queries = [
        f"{name} manufacturing plants facilities locations worldwide",
        f"{name} global operations factories revenue employees industry",
        f"site:{domain} locations facilities manufacturing",
    ]
    results = {}
    with DDGS() as ddg:
        for q in queries:
            try:
                results[q] = list(ddg.text(q, max_results=5))
                time.sleep(2)
            except:
                results[q] = []
                time.sleep(5)
    return results


def get_text(intel):
    parts = []
    for page in intel["sources"]["website"].values():
        parts.append(page.get("text", ""))
    parts.append(intel["sources"]["wikipedia"].get("text", ""))
    for val in intel["sources"]["sec_edgar"].values():
        if isinstance(val, str): parts.append(val)
    for hits in intel["sources"]["searches"].values():
        for h in hits:
            parts.append(h.get("title", "") + " " + h.get("snippet", ""))
    return " ".join(parts)


# ── STAGE 2: AI SCORING ───────────────────────────────────────────────────────

def ask_claude(name, text, api_key, status):
    status.update(label=f"🤖 Claude is analyzing {name}...")
    client = anthropic.Anthropic(api_key=api_key)

    prompt = f"""
You are a B2B sales analyst for TRACTIAN, which sells industrial IoT sensors
and predictive maintenance software to factories and industrial facilities.

Here is raw intelligence collected about this company:
Company: {name}
---
{text[:3000]}
---

Score this company and return ONLY a JSON object, no other text.

SCORING RULES:

manufacturing_score (0.0 to 3.0):
  3.0 = company OWNS and OPERATES heavy production facilities (chemical plants,
        food processing lines, refineries, aerospace assembly, steel mills)
  2.0 = owns some physical production but not the core business
  1.0 = light physical ops (roasting, bottling, small assembly)
  0.5 = outsources manufacturing to third parties or franchise model
  0.0 = pure digital/software/financial company

assets_score (0.0 to 3.0):
  3.0 = hundreds of owned plants, heavy machinery, massive capital equipment
  2.0 = dozens of owned facilities with significant equipment
  1.0 = a few offices or small facilities
  0.0 = virtually no physical assets

downtime_score (0.0 to 2.5):
  2.5 = stopping production = immediate massive financial loss
  1.5 = downtime is costly but company has buffers
  0.5 = downtime inconvenient but not financially critical
  0.0 = no physical production, downtime does not apply

global_score (0.0 to 1.5):
  1.5 = owned facilities on multiple continents
  1.0 = one country primary but meaningful international presence
  0.5 = mostly one country
  0.0 = single country only

KEY RULES:
- Outsourced manufacturing → manufacturing_score MAX 0.5
- Franchise model → manufacturing_score MAX 0.5
- Pure software/fintech/crypto/gaming → total = 1
- Warehouses only, no production → manufacturing_score MAX 1.0
- total_score = sum of all four, clamped 1 to 10
- Be conservative: only true heavy industrial companies with OWNED plants get 9-10

For locations, list every facility you can identify. Be aggressive — minimum 2-3 locations.

Return this exact JSON:
{{
  "manufacturing_score": <number>,
  "assets_score": <number>,
  "downtime_score": <number>,
  "global_score": <number>,
  "total_score": <number>,
  "reasoning": "<one sentence>",
  "locations": [
    {{
      "city": "<city>",
      "country": "<country>",
      "classification": "<Manufacturing Plant / Processing Plant / Packaging Plant / Refinery / Distribution Center / Research Center / Corporate HQ>",
      "confidence": "<high / medium / low>"
    }}
  ]
}}
"""

    msg = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = msg.content[0].text.strip().replace("```json", "").replace("```", "").strip()
    return json.loads(raw)


# ── MAIN PIPELINE ─────────────────────────────────────────────────────────────

def score_color(score):
    """Return a color based on score."""
    if score >= 8:   return "🟢"
    elif score >= 5: return "🟡"
    else:            return "🔴"


if run_button:
    if not company_name or not company_website:
        st.error("Please enter both a company name and website.")
    elif not api_key:
        st.error("Please enter your Anthropic API key in the sidebar.")
    else:
        with st.status("Running pipeline...", expanded=True) as status:

            # Stage 1: Collect data
            website_data = asyncio.run(scrape_website(company_name, company_website, status))
            wiki_data    = fetch_wikipedia(company_name, status)
            sec_data     = fetch_sec(company_name, status)
            search_data  = run_searches(company_name, company_website, status)

            intel = {
                "company_name":    company_name,
                "company_website": company_website,
                "sources": {
                    "website":   website_data,
                    "wikipedia": wiki_data,
                    "sec_edgar": sec_data,
                    "searches":  search_data,
                }
            }

            # Stage 2: Score with Claude
            text   = get_text(intel)
            result = ask_claude(company_name, text, api_key, status)
            status.update(label="✅ Done!", state="complete")

        # ── RESULTS ───────────────────────────────────────────────────────────
        st.divider()
        total = result["total_score"]

        # Score header
        col1, col2 = st.columns([1, 3])
        with col1:
            st.metric(
                label="ICP Score",
                value=f"{total}/10",
                delta="Strong fit" if total >= 8 else ("Moderate fit" if total >= 5 else "Poor fit")
            )
        with col2:
            st.markdown(f"### {score_color(total)} {company_name}")
            st.markdown(f"*{result['reasoning']}*")

        # Score breakdown
        st.subheader("Score Breakdown")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("🏭 Manufacturing",     f"{result['manufacturing_score']}/3.0")
        c2.metric("🏗️ Physical Assets",   f"{result['assets_score']}/3.0")
        c3.metric("⚡ Downtime Sensitivity", f"{result['downtime_score']}/2.5")
        c4.metric("🌍 Global Operations", f"{result['global_score']}/1.5")

        # Facilities table
        st.subheader("📍 Identified Facilities")
        locations = result.get("locations", [])
        if locations:
            df = pd.DataFrame([{
                "Company":           company_name,
                "Website":           company_website.replace("https://www.", ""),
                "Company Score":     f"{total}/10",
                "Facility Location": f"{loc['city']}, {loc['country']}".strip(", "),
                "Classification":    loc["classification"],
                "Confidence":        loc["confidence"],
            } for loc in locations])
            st.dataframe(df, use_container_width=True, hide_index=True)

            # Download button
            excel_path = f"{company_name.replace(' ', '_')}_output.xlsx"
            df.to_excel(excel_path, index=False)
            with open(excel_path, "rb") as f:
                st.download_button(
                    label="⬇️ Download Excel",
                    data=f,
                    file_name=excel_path,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True
                )
        else:
            st.warning("No specific facility locations found — try a more industrial company.")

        # Raw data expander
        with st.expander("🔍 View raw collected data"):
            st.json(intel["sources"]["wikipedia"])
