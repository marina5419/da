FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bot.py card_generator.py track_maker.py template_grunge.png card_font.ttf Stadium.otf make_templates.py ./
COPY templates/ ./templates/
COPY fonts/ ./fonts/
COPY brand/ ./brand/
ENV DB_PATH=/data/college_records.sqlite3
VOLUME ["/data"]
CMD ["python", "bot.py"]
