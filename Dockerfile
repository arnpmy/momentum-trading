# Momentum Trading System - Hyperliquid
# Multi-stage build for production deployment

FROM python:3.12-slim as builder

# Install Poetry
RUN pip install --no-cache-dir poetry==1.8.3

# Set working directory
WORKDIR /app

# Copy dependency files
COPY pyproject.toml poetry.lock ./

# Install dependencies (no dev packages)
RUN poetry config virtualenvs.in-project true && \
    poetry install --only main --no-interaction --no-ansi

# Production stage
FROM python:3.12-slim

WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /app/.venv /app/.venv

# Copy application code
COPY momentum_trading ./momentum_trading
COPY config ./config

# Add venv to PATH
ENV PATH="/app/.venv/bin:$PATH"

# Default to mainnet_test config
ENV CONFIG_FILE=config/mainnet_test.yaml

# Create data directories
RUN mkdir -p /app/data/state /app/data/cache /app/logs

# Volume for persistent data
VOLUME ["/app/data", "/app/logs"]

# Run the trading system (unbuffered output for Docker logs)
CMD ["sh", "-c", "python -u -m momentum_trading.main --config ${CONFIG_FILE}"]

