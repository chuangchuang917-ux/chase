FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install python dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose port (Cloud Run standard is 8080)
EXPOSE 8080

# Start adaptive Streamlit app binding to all interfaces and respecting dynamic $PORT
CMD ["sh", "-c", "streamlit run app.py --server.port ${PORT:-8080} --server.address 0.0.0.0"]
