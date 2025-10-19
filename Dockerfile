# 1. Começamos com uma imagem base leve do Python
FROM python:3.11-slim

# 2. Definimos um "diretório de trabalho" dentro do contêiner
WORKDIR /app

# 3. Copiamos APENAS o arquivo de requisitos primeiro
COPY requirements.txt .

# 4. Instalamos as dependências
RUN pip install --no-cache-dir -r requirements.txt

# 5. Copiamos todo o resto do seu projeto (bot.py, a pasta data/, etc.)
COPY . .

# 6. O comando que o Fly.io irá rodar quando o bot iniciar
CMD ["python", "bot.py"]