import re, time
import pandas as pd
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

URL = "https://seape.df.gov.br/foragidos/"
SEL_CARD = ".product-grid-item-content"
CSV_NAME = "foragidos.csv"

def norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

def extract_cidade(texto: str):
    m = re.search(r"Cidade\s*[:\-]?\s*([A-Za-zÁÀÂÃÉÊÍÓÔÕÚÇ\s\-]+)", norm(texto), flags=re.I)
    return norm(m.group(1)) if m else None

def try_accept_cookies(page):
    for sel in (
        '#onetrust-accept-btn-handler',
        '.ot-sdk-container #onetrust-accept-btn-handler',
        '.cli-accept-all', '#cn-accept-cookie',
        'button:has-text("Aceitar")', 'button:has-text("ACEITAR")',
        'button:has-text("Aceitar todos")'
    ):
        try:
            page.locator(sel).first.click(timeout=1200)
            page.wait_for_timeout(250)
            break
        except Exception:
            pass

def js_count(page, selector: str) -> int:
    try:
        return page.evaluate("s => document.querySelectorAll(s).length", selector)
    except Exception:
        return 0

def auto_scroll(page, rounds=80, pause_ms=650):
    last = -1
    stable = 0
    for i in range(rounds):
        cards = js_count(page, SEL_CARD)
        if cards == last: stable += 1
        else: stable = 0
        last = cards
        page.evaluate("window.scrollBy(0, Math.max(1200, window.innerHeight*1.5))")
        page.wait_for_timeout(pause_ms)
        if stable >= 6 and i >= 8:
            break
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    page.wait_for_timeout(900)

def collect_from_cards(page):
    rows = []
    cards = page.locator(SEL_CARD)
    for i in range(cards.count()):
        card = cards.nth(i)
        img = card.locator("img").first
        src = img.get_attribute("src")
        title = img.get_attribute("title")
        foto_url = urljoin(URL, src) if src else None
        nome = title.title() if title else None
        txt = card.inner_text(timeout=8000)
        cidade = extract_cidade(txt)
        # fallback do nome pelo texto
        if not nome:
            linhas = [ln.strip() for ln in txt.splitlines() if ln.strip()]
            limpas = [ln for ln in linhas if not re.search(
                r"Prontu[aá]rio|Cidade|Foragido desde|Visualizar|Clique Aqui|Pol[ií]cia|Penal|DCCP|DEPATE",
                ln, re.I)]
            ups = [ln for ln in limpas if ln.isupper() and len(ln) >= 5]
            nome = (max(ups, key=len) if ups else (limpas[0] if limpas else None))
            if nome and nome.isupper(): nome = nome.title()
        rows.append({"ordem": i+1, "nome": nome, "cidade": cidade, "foto_url": foto_url})
    return rows

def collect_from_images(page):
    rows = []
    imgs = page.locator('img[title], img[src*="imageminterno"]')
    for i in range(imgs.count()):
        img = imgs.nth(i)
        src = img.get_attribute("src")
        title = img.get_attribute("title")
        foto_url = urljoin(URL, src) if src else None
        texto = img.evaluate("""el => {
            const card = el.closest('.product-grid-item-content');
            return (card ? card.innerText : el.parentElement?.innerText) || '';
        }""")
        cidade = extract_cidade(texto or "")
        nome = title.title() if title else None
        if not (nome or cidade or foto_url): continue
        rows.append({"ordem": i+1, "nome": nome, "cidade": cidade, "foto_url": foto_url})
    return rows

def main():
    all_rows = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=['--no-sandbox','--disable-setuid-sandbox','--disable-dev-shm-usage',
                  '--disable-gpu','--disable-blink-features=AutomationControlled']
        )
        ctx = browser.new_context(
            viewport={"width": 1366, "height": 2400},
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"),
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
        )
        # “stealth” básico
        ctx.add_init_script('Object.defineProperty(navigator, "webdriver", {get: () => undefined})')
        ctx.add_init_script("""
            // mimetypes/plugins fake
            Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3]});
            Object.defineProperty(navigator, 'languages', {get: () => ['pt-BR','pt','en-US','en']});
        """)

        page = ctx.new_page()
        # abre a home e força navegar para a rota exata da aba
        page.goto("https://seape.df.gov.br/", wait_until="domcontentloaded", timeout=120000)
        try:
            page.locator('a[href$="/foragidos/"]').first.click(timeout=4000)
            page.wait_for_url("**/foragidos/**", timeout=10000)
        except Exception:
            page.goto(URL, wait_until="domcontentloaded", timeout=120000)

        try_accept_cookies(page)

        # aguarda algo do card existir e rola
        try:
            page.wait_for_selector(SEL_CARD, state="attached", timeout=60000)
        except PWTimeout:
            pass
        auto_scroll(page)

        # tenta pelos cards
        if page.locator(SEL_CARD).count() > 0:
            all_rows = collect_from_cards(page)

        # fallback por imagens
        if not all_rows:
            all_rows = collect_from_images(page)

        # saída
        pd.DataFrame(all_rows).to_csv(CSV_NAME, index=False, encoding="utf-8")
        print(f"✅ CSV salvo: {CSV_NAME} | {len(all_rows)} registros")

        # artefatos de debug
        with open("debug.html", "w", encoding="utf-8") as f:
            f.write(page.content())
        page.screenshot(path="debug.png", full_page=True)

        ctx.close(); browser.close()

if __name__ == "__main__":
    main()
