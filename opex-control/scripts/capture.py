import io
import sys
import time
from pathlib import Path

from PIL import Image
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8050"
OUT = Path(__file__).resolve().parent.parent.parent / "docs" / "opex-control" / "media"
OUT.mkdir(parents=True, exist_ok=True)
frames = []


def driver():
    o = webdriver.ChromeOptions()
    o.add_argument("--headless=new")
    o.add_argument("--window-size=1440,1250")
    o.add_argument("--hide-scrollbars")
    o.add_argument("--force-color-profile=srgb")
    return webdriver.Chrome(options=o)


def shot(d, name, pause=1.8):
    time.sleep(pause)
    png = d.get_screenshot_as_png()
    (OUT / f"{name}.png").write_bytes(png)
    frames.append(Image.open(io.BytesIO(png)).convert("RGB").resize((960, 833)))
    print("shot", name)


def wait(d, css, t=20):
    return WebDriverWait(d, t).until(EC.presence_of_element_located((By.CSS_SELECTOR, css)))


def tab(d, label):
    for el in d.find_elements(By.CSS_SELECTOR, ".tab"):
        if el.text.strip() == label:
            el.click()
            return
    raise RuntimeError(label)


def main():
    d = driver()
    try:
        d.get(URL)
        wait(d, "#login-user")
        shot(d, "01-login")
        d.find_element(By.ID, "login-user").send_keys("admin")
        d.find_element(By.ID, "login-pass").send_keys("admin123")
        d.find_element(By.ID, "login-btn").click()
        wait(d, ".kpi")
        shot(d, "02-summary", 3.0)

        tab(d, "Hierarchy")
        wait(d, "#h-grid")
        shot(d, "03-hierarchy-division", 2.5)
        d.find_element(By.CSS_SELECTOR, "#h-grid td.dash-cell").click()
        shot(d, "04-hierarchy-region", 2.5)
        d.find_element(By.CSS_SELECTOR, "#h-grid td.dash-cell").click()
        shot(d, "05-hierarchy-zone", 2.5)

        tab(d, "Accounts")
        shot(d, "06-accounts", 2.5)
        tab(d, "Alerts")
        wait(d, "#al-grid")
        shot(d, "07-alerts", 2.5)
        d.find_element(By.CSS_SELECTOR, "#al-grid td.dash-cell").click()
        shot(d, "08-alert-series", 2.5)
        tab(d, "Timeline")
        shot(d, "09-timeline", 2.5)
        tab(d, "Pivot")
        wait(d, "#p-grid")
        shot(d, "10-pivot", 2.5)
        tab(d, "Cashi assistant")
        q = wait(d, "#c-q")
        for text in ["Top 5 stores over plan in Logistics", "Which anomalies happened in week 30?"]:
            q.clear()
            q.send_keys(text, Keys.ENTER)
            time.sleep(1.5)
        shot(d, "11-assistant", 2.5)
        tab(d, "Summary")
        time.sleep(1)
        dd = d.find_element(By.CSS_SELECTOR, "#f-div")
        dd.click()
        time.sleep(0.5)
        webdriver.ActionChains(d).send_keys(Keys.ARROW_DOWN, Keys.ENTER, Keys.ESCAPE).perform()
        time.sleep(1)
        shot(d, "12-summary-filtered", 3.0)
    finally:
        d.quit()
    if frames:
        frames[0].save(OUT / "tour.gif", save_all=True, append_images=frames[1:], duration=1400, loop=0, optimize=True)
        print("gif", len(frames))


if __name__ == "__main__":
    main()
