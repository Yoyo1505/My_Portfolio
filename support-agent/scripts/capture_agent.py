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

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8061"
TAG = sys.argv[2] if len(sys.argv) > 2 else "mock"
OUT = Path(__file__).resolve().parent.parent.parent / "docs" / "support-agent" / "media"
OUT.mkdir(parents=True, exist_ok=True)
QUESTIONS = [("u001", "When is my next payment for the Northline order and how much is it?"),
             ("u001", "How do refunds work?"),
             ("u005", "Please waive my late fee"),
             ("u002", "What are the details of order ord_5001?")]


def main():
    o = webdriver.ChromeOptions()
    o.add_argument("--headless=new")
    o.add_argument("--window-size=1280,860")
    d = webdriver.Chrome(options=o)
    frames = []
    try:
        for i, (uid, q) in enumerate(QUESTIONS, 1):
            d.get(f"{URL}/?user={uid}")
            WebDriverWait(d, 20).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".order")))
            box = d.find_element(By.ID, "q")
            box.send_keys(q, Keys.ENTER)
            WebDriverWait(d, 900).until(lambda dr: "Thinking" not in dr.find_element(By.ID, "thread").text and len(dr.find_elements(By.CSS_SELECTOR, ".b")) >= 1)
            time.sleep(1.0)
            png = d.get_screenshot_as_png()
            (OUT / f"{TAG}-{i:02d}.png").write_bytes(png)
            frames.append(Image.open(io.BytesIO(png)).convert("RGB").resize((960, 645)))
            print("shot", i)
    finally:
        d.quit()
    frames[0].save(OUT / f"{TAG}-tour.gif", save_all=True, append_images=frames[1:], duration=2200, loop=0, optimize=True)


if __name__ == "__main__":
    main()
