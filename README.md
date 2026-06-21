# MediTrack — Lični zdravstveni asistent

Privatni zdravstveni tracker (Streamlit) sa AI „Health Guard" zaštitom.
Smart Camera → Google Cloud Vision (OCR) prepozna sa fotografije da li je
merač pritiska, deklaracija hrane ili medicinski nalaz, a Claude (Anthropic)
obradi i smesti podatak na pravo mesto. Vitalni znaci, trendovi (1/7/30 dana)
i personalizovane procene — sve u neon/glass GUI-ju.

## Pokretanje lokalno
```bash
pip install -r requirements.txt
streamlit run app.py
```
Ključeve uneti u bočnoj traci (⚙️) ili kao env varijable
`ANTHROPIC_API_KEY` i `GOOGLE_VISION_API_KEY`.

## Deploy na Streamlit Community Cloud
1. [share.streamlit.io](https://share.streamlit.io) → **Create app** → izaberi ovaj repo, grana `main`, fajl `app.py`.
2. **Advanced settings → Secrets** → nalepi (vidi `.streamlit/secrets.toml.example`):
   ```toml
   ANTHROPIC_API_KEY = "sk-ant-..."
   GOOGLE_VISION_API_KEY = "AIza..."
   app_password = "tvoja-lozinka"   # zaštita javnog linka
   ```
3. **Deploy** → dobiješ HTTPS link koji radi na telefonu (živa kamera radi).

## Privatnost
Lokalna SQLite baza (`*.db`), API ključevi i `secrets.toml` **nisu** u repo-u
(vidi `.gitignore`). Na cloud-u podaci se čuvaju u bazi te instance.

> Savetodavna alatka — nije zamena za lekara.
