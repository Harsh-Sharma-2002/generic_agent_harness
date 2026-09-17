"""Shared pytest setup: load the gitignored .env before any test collects."""

from dotenv import load_dotenv

load_dotenv()
