# scrape_foragidos.py
# -------------------------------------------------------
# Coleta todos os cards da página Foragidos (SEAPE-DF)
# e gera foragidos.csv com: ordem, nome, cidade, foto_url.
# Se não encontrar nada, salva debug.html e debug.png.
# -------------------------------------------------------

import re
import pandas as pd
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

URL = "https://seape.df.gov.br/foragidos/"
SEL_CARD = ".product-grid-item-content"
CSV_NAME = "foragidos.csv"

# ---------- util ----------
def norm(s: str) -> str:
    import re as _re
    return _re.sub(r"\s+", " ", (s or "").strip())

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
            print(f"[cookies] clique em {sel}")
            break
        except Exception:
            pass

def js_count(page, selector: str) -> int:
    try:
        return page.evaluate("s => document.querySelectorAll(s).length", selector)
    except Exception:
        return 0

def auto_scroll_until_stable(page, min_rounds=6, max_rounds=90, pause_ms=650):
    """Rola a página até a contagem dos cards estabilizar por algumas iterações."""
    last, stable = -1, 0
    for i in range(max_rounds):
        cards = js_count(page, SEL_CARD)
        imgs_title = js_count(page, 'img[title]')
        imgs_internal = js_count(page, 'img[src*="imageminterno"]')
        print(f"[scroll] {i:02d} -> cards={cards} | img[title]={imgs_title} | img[src*=imageminterno]={imgs_internal}")
        stable = stable + 1 if cards == last else 0
        last = cards
        page.evaluate("window.scrollBy(0, Math.max(1200, window.innerHeight*1.5))")
        page.wait_for_timeout(pause_ms)
        if stable >= 5 and i >= min_rounds:
            break
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    page.wait_for_timeout(900)
    return js_count(page, SEL_CARD)

# ---------- coleta ----------
def collect_from_cards(page):
    """Coleta dados iterando por .product-grid-item-content."""
    rows = []
    cards = page.locator(SEL_CARD)
    n = cards.count()
    for i in range(n):
        card = cards.nth(i)
        # nome e foto direto do <img> quando disponível
        img = card.locator("img").first
        src = img.get_attribute("src")
        title = img.get_attribute("title")
        foto_url = urljoin(URL, src) if src else None
        nome = title.title() if title else None

        txt = card.inner_text(timeout=8000)
        cidade = extract_cidade(txt)

        # fallback de nome (maior linha em CAPS que não seja rótulo)
        if not nome:
            linhas = [ln.strip() for ln in txt.splitlines() if ln.strip()]
            limpas = [ln for ln in linhas if not re.search(
                r"Prontu[aá]rio|Cidade|Foragido desde|Visualizar|Clique Aqui|Pol[ií]cia|Penal|DCCP|DEPATE",
                ln, re.I)]
            ups = [ln for ln in limpas if ln.isupper() and len(ln) >= 5]
            nome = (max(ups, key=len) if ups else (limpas[0] if limpas else None))
            if nome and nome.isupper():
                nome = nome.title()

        rows.append({
            "ordem": i + 1,
            "nome": nome,
            "cidade": cidade,
            "foto_url": foto_url
        })
    return rows

def collect_from_images(page):
    """Fallback: pega imagens com title/src e tenta extrair cidade do container."""
    rows = []
    imgs = page.locator('img[title], img[src*="imageminterno"]')
    n = imgs.count()
    print(f"[fallback] imagens candidatas: {n}")
    for i in range(n):
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
        if not (nome or cidade or foto_url):
            continue
        rows.append({"ordem": i + 1, "nome": nome, "cidade": cidade, "foto_url": foto_url})
    return rows

# ---------- main ----------
def main():
    all_rows = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox','--disable-setuid-sandbox',
                '--disable-dev-shm-usage','--disable-gpu',
                '--disable-blink-features=AutomationControlled'
            ]
        )
        ctx = browser.new_context(
            viewport={"width": 1366, "height": 2400},
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"),
            locale="pt-BR", timezone_id="America/Sao_Paulo",
        )
        page = ctx.new_page()
        page.goto(URL, wait_until="domcontentloaded", timeout=120000)
        try_accept_cookies(page)

        # tenta esperar o seletor de card, mas segue mesmo se não aparecer
        try:
            page.wait_for_selector(SEL_CARD, state="attached", timeout=90000)
        except PWTimeout:
            print("[warn] cards não apareceram em 90s; seguindo com scroll.")

        total_cards = auto_scroll_until_stable(page)
        print(f"[final] contagem por seletor: {total_cards}")

        # 1) tentativa principal
        if total_cards > 0:
            all_rows = collect_from_cards(page)

        # 2) fallback por imagens
        if not all_rows:
            print("[info] ativando fallback por imagens…")
            all_rows = collect_from_images(page)

        # salva CSV e artefatos de debug
        pd.DataFrame(all_rows).to_csv(CSV_NAME, index=False, encoding="utf-8")
        print(f"✅ CSV salvo: {CSV_NAME} | {len(all_rows)} registros")

        if len(all_rows) == 0:
            with open("debug.html", "w", encoding="utf-8") as f:
                f.write(page.content())
            page.screenshot(path="debug.png", full_page=True)
            print("⚠️ Nada coletado. Salvos debug.html e debug.png para diagnóstico.")

        ctx.close(); browser.close()

if __name__ == "__main__":
    main()
