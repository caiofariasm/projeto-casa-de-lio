FROM python:3.12-slim

# Evita geração de arquivos .pyc e faz o stdout/stderr saírem sem buffer
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Instala dependências do sistema necessárias para compilação eventual
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Instalação das dependências Python em camada separada para aproveitar cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia todo o código-fonte da aplicação
COPY . .

# Cria os diretórios necessários
RUN mkdir -p /app/uploads

# Expõe a porta interna da aplicação FastAPI
EXPOSE 8000

# Inicializa o servidor ASGI Uvicorn
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
