"""
TRACTIAN GTM Dashboard
======================
Streamlit Cloud compatible version (no Playwright).
Uses requests + BeautifulSoup for web scraping.

Deploy on Streamlit Cloud:
  1. Push this file + requirements.txt to GitHub
  2. Go to share.streamlit.io and connect your repo
  3. Done!
"""

import os, json, re, time
import requests
import pandas as pd
import anthropic
import streamlit as st
from bs4 import BeautifulSoup
from duckduckgo_search import DDGS

# ── PAGE CONFIG ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="TRACTIAN Account Scorer",
    page_icon="🏭",
    layout="wide"
)

# ── HEADER ────────────────────────────────────────────────────────────────────
st.title("🏭 TRACTIAN Account Scorer")
st.caption("Enter a company name and website to score their ICP fit and map their facilities.")

# ── SIDEBAR ───────────────────────────────────────────────────────────────────
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
    st.divider()
    st.markdown("**How it works:**")
    st.markdown("1. Scrapes company website")
    st.markdown("2. Pulls Wikipedia data")
    st.markdown("3. Checks SEC EDGAR filings")
    st.markdown("4. Searches the web")
    st.markdown("5. Claude AI scores & classifies")


# ── INPUT ─────────────────────────────────────────────────────────────────────
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
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
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
    except:
        return None


# ── STAGE 1: DATA COLLECTION ──────────────────────────────────────────────────

def scrape_website(name, website, progress_text):
    progress_text.write("🌐 Scraping website pages...")
    base  = clean_base_url(website)
    found = {}

    for path in PAGES_TO_TRY:
        url  = base + path
        html = simple_fetch(url)
        if html and len(html) > 500:
            text = html_to_text(html)
            if len(text) > 200:
                found[path or "/"] = {"url": url, "text": text}
        time.sleep(0.3)

    return found


def fetch_wikipedia(name, progress_text):
    progress_text.write("📖 Checking Wikipedia...")
    url  = f"https://en.wikipedia.org/wiki/{name.replace(' ', '_')}"
    html = simple_fetch(url)

    if not html or "may refer to" in (html or "") or "disambiguation" in (html or ""):
        api  = (
            f"https://en.wikipedia.org/w/api.php?action=query&list=search"
            f"&srsearch={requests.utils.quote(name + ' company')}&format=json&srlimit=1"
        )
        data = simple_fetch(api)
        if data:
            try:
                hits = json.loads(data).get("query", {}).get("search", [])
                if hits:
                    title = hits[0]["title"].replace(" ", "_")
                    url   = f"https://en.wikipedia.org/wiki/{title}"
                    html  = simple_fetch(url)
            except:
                pass

    if html:
        return {"url": url, "text": html_to_text(html, max_chars=5000)}
    return {}


def fetch_sec(name, progress_text):
    progress_text.write("📋 Checking SEC EDGAR filings...")
    url  = (
        f"https://efts.sec.gov/LATEST/search-index"
        f"?q=%22{requests.utils.quote(name)}%22&forms=10-K&dateRange=custom&startdt=2023-01-01"
    )
    html = simple_fetch(url)
    return {"search_results": html[:3000]} if html else {}


def run_searches(name, website, progress_text):
    progress_text.write("🔎 Searching the web...")
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

def ask_claude(name, text, api_key, progress_text):
    progress_text.write("🤖 Claude is analyzing and scoring...")
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


# ── DISPLAY HELPERS ───────────────────────────────────────────────────────────

def score_badge(score):
    if score >= 8:   return "🟢 Strong fit"
    elif score >= 5: return "🟡 Moderate fit"
    else:            return "🔴 Poor fit"


# ── MAIN PIPELINE ─────────────────────────────────────────────────────────────

if run_button:
    if not company_name or not company_website:
        st.error("Please enter both a company name and website.")
    elif not api_key:
        st.error("Please enter your Anthropic API key in the sidebar.")
    else:
        # Progress container
        with st.container():
            progress_text = st.empty()
            progress_bar  = st.progress(0)

            # Stage 1: Collect
            website_data = scrape_website(company_name, company_website, progress_text)
            progress_bar.progress(25)

            wiki_data = fetch_wikipedia(company_name, progress_text)
            progress_bar.progress(50)

            sec_data = fetch_sec(company_name, progress_text)
            progress_bar.progress(60)

            search_data = run_searches(company_name, company_website, progress_text)
            progress_bar.progress(80)

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

            # Stage 2: Score
            text   = get_text(intel)
            result = ask_claude(company_name, text, api_key, progress_text)
            progress_bar.progress(100)
            progress_text.empty()
            progress_bar.empty()

        # ── RESULTS ───────────────────────────────────────────────────────────
        st.divider()
        total = result["total_score"]

        # Score header
        col1, col2 = st.columns([1, 3])
        with col1:
            st.metric(label="ICP Score", value=f"{total}/10")
            st.markdown(score_badge(total))
        with col2:
            st.markdown(f"### {company_name}")
            st.markdown(f"*{result['reasoning']}*")

        # Score breakdown
        st.subheader("Score Breakdown")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("🏭 Manufacturing",        f"{result['manufacturing_score']}/3.0")
        c2.metric("🏗️ Physical Assets",      f"{result['assets_score']}/3.0")
        c3.metric("⚡ Downtime Sensitivity", f"{result['downtime_score']}/2.5")
        c4.metric("🌍 Global Operations",    f"{result['global_score']}/1.5")

        # Facilities table
        st.subheader("📍 Identified Facilities")
        locations = result.get("locations", [])

        if locations:
            df = pd.DataFrame([{
                "Company":           company_name,
                "Website":           company_website.replace("https://www.", "").replace("https://", ""),
                "Company Score":     f"{total}/10",
                "Facility Location": f"{loc['city']}, {loc['country']}".strip(", "),
                "Classification":    loc["classification"],
                "Confidence":        loc["confidence"],
            } for loc in locations])

            st.dataframe(df, use_container_width=True, hide_index=True)

            # Excel download
            from io import BytesIO
            buffer = BytesIO()
            df.to_excel(buffer, index=False)
            buffer.seek(0)
            st.download_button(
                label="⬇️ Download Excel",
                data=buffer,
                file_name=f"{company_name.replace(' ', '_')}_tractian_output.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True
            )
        else:
            st.warning("No specific locations found.")

        # Raw data (optional)
        with st.expander("🔍 View raw collected data"):
            tab1, tab2, tab3 = st.tabs(["Wikipedia", "Website Pages", "Search Results"])
            with tab1:
                st.write(intel["sources"]["wikipedia"].get("text", "No data")[:2000])
            with tab2:
                for path, data in intel["sources"]["website"].items():
                    st.markdown(f"**{path}** — {data['url']}")
            with tab3:
                for query, hits in intel["sources"]["searches"].items():
                    st.markdown(f"**{query[:60]}**")
                    for h in hits[:3]:
                        st.markdown(f"- {h.get('title','')} — {h.get('href','')}")
