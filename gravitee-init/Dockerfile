FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir requests pyyaml
# Copy files individually so Docker layer cache is busted when content changes
COPY *.py ./
COPY apim-apis/ ./apim-apis/
COPY am-apps/ ./am-apps/
COPY am-mcp-servers/ ./am-mcp-servers/
CMD ["python", "main.py"]
