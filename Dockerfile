FROM debian:stable
WORKDIR /linkedin-follower

RUN apt update && apt upgrade -y && apt install -y wget gnupg python3 python3-pip python3-venv chromium

RUN python3 -m venv .venv
COPY requirements.txt ./
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

COPY Enrich.py ./Enrich.py

RUN useradd app
USER app

CMD ["python3", "./Enrich.py"]