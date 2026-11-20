import io
import sys
import time
from pathlib import Path

from PIL import Image
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8050"
OUT = Path(__file__).resolve().parent.parent.parent / "docs" / "dash-app" / "media"
OUT.mkdir(parents=True, exist_ok=True)
frames = []


def shot(d, name, pause=3.0):
    time.sleep(pause)
    png = d.get_screenshot_as_png()
    (OUT / f"{name}.png").write_bytes(png)
    frames.append(Image.open(io.BytesIO(png)).convert("RGB").resize((960, 600)))
    print("shot", name, flush=True)


def main():
    o = webdriver.ChromeOptions()
    o.add_argument("--headless=new")
    o.add_argument("--window-size=1600,1000")
    o.add_argument("--hide-scrollbars")
    d = webdriver.Chrome(options=o)
    try:
        d.get(URL)
        WebDriverWait(d, 30).until(EC.presence_of_element_located((By.ID, "login-input-user")))
        shot(d, "01-login", 1.5)
        d.find_element(By.ID, "login-input-user").send_keys("admin1")
        d.find_element(By.ID, "login-input-password").send_keys("admin123")
        d.find_element(By.ID, "login-btn-submit").click()
        time.sleep(6)
        shot(d, "02-after-login", 4)
        names = ["Resumen", "Cuenta", "PDV", "CECO", "Contraloría", "Cierres", "Tiempo", "Comparar", "Dinámica", "Cashi", "Alertas", "Usuarios"]
        for i, label in enumerate(names, 3):
            clicked = False
            for el in d.find_elements(By.CSS_SELECTOR, ".nav-tabs-custom .nav-link"):
                if (el.get_attribute("textContent") or "").strip() == label:
                    d.execute_script("arguments[0].click()", el)
                    clicked = True
                    break
            if not clicked:
                print("missing tab", label)
                continue
            shot(d, f"{i:02d}-{label.lower().replace('í', 'i').replace('á', 'a')}", 5)
            if label == "Cashi":
                for k, q in enumerate(["Real 2026 vs Plan 2026", "Top 10 CECOs"]):
                    els = d.find_elements(By.XPATH, "//*[contains(normalize-space(.), '" + q + "') and not(*)]")
                    print("chip", q, len(els))
                    if els:
                        d.execute_script("arguments[0].click()", els[0])
                        shot(d, f"12{'bc'[k]}-cashi-{k + 1}", 9)
        for el in d.find_elements(By.CSS_SELECTOR, ".nav-tabs-custom .nav-link"):
            if (el.get_attribute("textContent") or "").strip() == "Resumen":
                d.execute_script("arguments[0].click()", el)
        time.sleep(2)
        for el in d.find_elements(By.XPATH, "//*[contains(normalize-space(.), 'Oscuro') and not(*)]"):
            d.execute_script("arguments[0].click()", el)
            break
        shot(d, "15-dark-theme", 4)
    finally:
        d.quit()
    frames[0].save(OUT / "tour.gif", save_all=True, append_images=frames[1:], duration=1800, loop=0, optimize=True)


if __name__ == "__main__":
    main()
