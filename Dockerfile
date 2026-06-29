FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install python dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose ports for desktop and mobile apps
EXPOSE 8501 8502

# Start both Streamlit apps
CMD streamlit run app.py --server.port 8501 & \
    streamlit run app_mobile.py --server.port 8502
