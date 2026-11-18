#!/bin/bash
set -e

ollama serve &

until curl -s http://localhost:11434 > /dev/null; do
  echo "Waiting for Ollama to start..."
  sleep 2
done

ollama pull nomic-embed-text
ollama pull llama3.2:1b

./db
